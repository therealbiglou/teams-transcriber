# Phase 11 — Wrike Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-sync every meeting's todos (my todos + action items for others) to Wrike on `SummaryReady` via a toast-driven folder picker, with one-way close-loop completion (app → Wrike) when a todo checkbox is toggled.

**Architecture:** Stateless `httpx` REST client (`integrations/wrike_client.py`) + per-recording orchestrator (`integrations/wrike_sync.py`) that persists mappings to two new SQLite tables (`schema_v4`). UI surfaces via a toast → themed folder-picker dialog → background-thread sync. Permanent Access Token stored in keyring; no token ever flows through chat.

**Tech Stack:** Python 3.11, PySide6, SQLite, `httpx` (already transitive via `anthropic`), `keyring`. Run tests with `& "C:\Users\brian\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest`.

Spec: `docs/superpowers/specs/2026-06-07-phase-11-wrike-integration-design.md`.

---

## File Structure

**Create:**
- `src/teams_transcriber/integrations/__init__.py` — package marker.
- `src/teams_transcriber/integrations/wrike_client.py` — REST client + typed exceptions.
- `src/teams_transcriber/integrations/wrike_sync.py` — per-recording sync orchestrator.
- `src/teams_transcriber/storage/schema_v4.py` — migration (pure CREATE TABLE additions).
- `src/teams_transcriber/storage/wrike.py` — `WrikeSyncRepo` and `WrikeTaskRepo`.
- `src/teams_transcriber/ui/wrike_folder_picker.py` — themed picker dialog.
- `tests/integrations/__init__.py`, `tests/integrations/test_wrike_client.py`, `tests/integrations/test_wrike_sync.py`.
- `tests/storage/test_wrike_repos.py`, `tests/storage/test_schema_v4_migration.py`.
- `tests/ui/test_wrike_folder_picker.py`.

**Modify:**
- `src/teams_transcriber/storage/__init__.py` — append `SCHEMA_V4` to `ALL_MIGRATIONS`, export the repos.
- `src/teams_transcriber/config.py` — add `integrations.wrike_enabled: bool` (default False) + `integrations.wrike_recent_folder_ids: list[str]` (default []) settings + `KEYRING_USER_WRIKE` constant.
- `src/teams_transcriber/ui/settings_dialog.py` — new "Integrations" tab between AI and Shortcuts.
- `src/teams_transcriber/ui/app.py` — `SummaryReady` toast/picker, `todo_state_changed` close-loop, startup pending check.

---

## Task 1: schema_v4 + Wrike repos

**Files:**
- Create: `src/teams_transcriber/storage/schema_v4.py`
- Create: `src/teams_transcriber/storage/wrike.py`
- Modify: `src/teams_transcriber/storage/__init__.py`
- Test: `tests/storage/test_schema_v4_migration.py`, `tests/storage/test_wrike_repos.py`

- [ ] **Step 1: Write failing migration data-safety test**

```python
# tests/storage/test_schema_v4_migration.py
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import build_database
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus,
)
from teams_transcriber.storage.recordings import RecordingRepo


def test_v4_migration_preserves_recordings_and_adds_tables(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-07T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    # The two new tables exist after migration.
    cur = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = {row[0] for row in cur.fetchall()}
    assert "wrike_sync" in names
    assert "wrike_tasks" in names
    # CASCADE works: deleting the recording removes any wrike rows for it.
    db.conn.execute(
        "INSERT INTO wrike_sync (recording_id, folder_id, status) "
        "VALUES (?, ?, ?)", (rec.id, "F1", "synced"),
    )
    db.conn.execute(
        "INSERT INTO wrike_tasks "
        "(recording_id, kind, todo_index, wrike_task_id, wrike_folder_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (rec.id, "my", 0, "T1", "F1", "2026-06-07T10:00:00Z"),
    )
    db.conn.commit()
    RecordingRepo(db).delete(rec.id)
    assert db.conn.execute("SELECT COUNT(*) FROM wrike_sync").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM wrike_tasks").fetchone()[0] == 0
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "<uv>" run pytest tests/storage/test_schema_v4_migration.py -v`
Expected: FAIL — `no such table: wrike_sync`.

- [ ] **Step 3: Implement schema_v4 and register it**

Create `src/teams_transcriber/storage/schema_v4.py`:
```python
"""Schema v4: add wrike_sync and wrike_tasks tables for the Wrike integration.

Pure CREATE additions — no existing-table CHECK changes — so we don't need the
table-rebuild dance from v3. ON DELETE CASCADE both tables from recordings so
the mappings disappear with the source recording. The Wrike tasks themselves
are left in place in Wrike (the user owns them there).
"""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE wrike_sync (
        recording_id      INTEGER PRIMARY KEY
                          REFERENCES recordings(id) ON DELETE CASCADE,
        folder_id         TEXT,
        status            TEXT NOT NULL CHECK (status IN
                              ('pending', 'synced', 'failed', 'skipped')),
        last_attempted_at TEXT,
        error_message     TEXT
    )
    """,
    """
    CREATE TABLE wrike_tasks (
        id                INTEGER PRIMARY KEY,
        recording_id      INTEGER NOT NULL
                          REFERENCES recordings(id) ON DELETE CASCADE,
        kind              TEXT NOT NULL CHECK (kind IN ('my', 'other')),
        todo_index        INTEGER NOT NULL,
        wrike_task_id     TEXT NOT NULL,
        wrike_folder_id   TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        last_synced_done  INTEGER NOT NULL DEFAULT 0,
        UNIQUE (recording_id, kind, todo_index)
    )
    """,
    "CREATE INDEX wrike_tasks_recording_idx ON wrike_tasks (recording_id)",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V4 = Migration(version=4, name="add wrike integration tables", apply=_apply)
```

Update `src/teams_transcriber/storage/__init__.py`:
```python
from teams_transcriber.storage.schema_v4 import SCHEMA_V4
...
ALL_MIGRATIONS: tuple[Migration, ...] = (SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4)
```

Also add `SCHEMA_V4` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `& "<uv>" run pytest tests/storage/test_schema_v4_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Write failing repo tests**

```python
# tests/storage/test_wrike_repos.py
import pytest

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import build_database
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.wrike import (
    WrikeSyncRepo, WrikeTaskRepo, WrikeSyncRow, WrikeTaskRow,
)


@pytest.fixture
def db_with_recording(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-07T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    yield db, rec.id
    db.close()


def test_wrike_sync_upsert_get_update(db_with_recording):
    db, rid = db_with_recording
    repo = WrikeSyncRepo(db)
    repo.upsert(rid, status="pending")
    row = repo.get(rid)
    assert row is not None and row.status == "pending" and row.folder_id is None
    repo.update(rid, status="synced", folder_id="F1")
    row = repo.get(rid)
    assert row.status == "synced" and row.folder_id == "F1"


def test_wrike_sync_list_pending_includes_failed(db_with_recording):
    db, rid = db_with_recording
    WrikeSyncRepo(db).upsert(rid, status="failed", error_message="boom")
    pending = WrikeSyncRepo(db).list_pending_or_failed()
    assert any(r.recording_id == rid for r in pending)


def test_wrike_task_insert_and_list(db_with_recording):
    db, rid = db_with_recording
    repo = WrikeTaskRepo(db)
    repo.insert(WrikeTaskRow(
        id=None, recording_id=rid, kind="my", todo_index=0,
        wrike_task_id="T1", wrike_folder_id="F1",
        created_at="2026-06-07T10:00:00Z", last_synced_done=False,
    ))
    rows = repo.list_for_recording(rid)
    assert len(rows) == 1 and rows[0].wrike_task_id == "T1"
    assert repo.get(rid, "my", 0).wrike_task_id == "T1"
    assert repo.get(rid, "my", 1) is None


def test_wrike_task_update_last_synced(db_with_recording):
    db, rid = db_with_recording
    repo = WrikeTaskRepo(db)
    repo.insert(WrikeTaskRow(
        id=None, recording_id=rid, kind="my", todo_index=0,
        wrike_task_id="T1", wrike_folder_id="F1",
        created_at="2026-06-07T10:00:00Z", last_synced_done=False,
    ))
    repo.set_last_synced_done(rid, "my", 0, True)
    assert repo.get(rid, "my", 0).last_synced_done is True
```

- [ ] **Step 6: Run repo tests to verify they fail**

Run: `& "<uv>" run pytest tests/storage/test_wrike_repos.py -v`
Expected: FAIL — repos missing.

- [ ] **Step 7: Implement the repos**

Create `src/teams_transcriber/storage/wrike.py`:
```python
"""Repos for the wrike_sync and wrike_tasks tables (schema v4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from teams_transcriber.storage.db import Database


@dataclass(slots=True)
class WrikeSyncRow:
    recording_id: int
    folder_id: str | None
    status: str   # 'pending' | 'synced' | 'failed' | 'skipped'
    last_attempted_at: str | None
    error_message: str | None


@dataclass(slots=True)
class WrikeTaskRow:
    id: int | None
    recording_id: int
    kind: str   # 'my' | 'other'
    todo_index: int
    wrike_task_id: str
    wrike_folder_id: str
    created_at: str
    last_synced_done: bool


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


class WrikeSyncRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, recording_id: int) -> WrikeSyncRow | None:
        cur = self._db.conn.execute(
            "SELECT recording_id, folder_id, status, last_attempted_at, "
            "error_message FROM wrike_sync WHERE recording_id = ?",
            (recording_id,),
        )
        row = cur.fetchone()
        return None if row is None else WrikeSyncRow(*row)

    def upsert(
        self,
        recording_id: int,
        *,
        status: str,
        folder_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self._db.conn.execute(
            "INSERT INTO wrike_sync (recording_id, folder_id, status, "
            "last_attempted_at, error_message) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(recording_id) DO UPDATE SET folder_id=excluded.folder_id, "
            "status=excluded.status, last_attempted_at=excluded.last_attempted_at, "
            "error_message=excluded.error_message",
            (recording_id, folder_id, status, _now_utc(), error_message),
        )
        self._db.conn.commit()

    def update(
        self,
        recording_id: int,
        *,
        status: str,
        folder_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.upsert(recording_id, status=status,
                    folder_id=folder_id, error_message=error_message)

    def list_pending_or_failed(self) -> list[WrikeSyncRow]:
        cur = self._db.conn.execute(
            "SELECT recording_id, folder_id, status, last_attempted_at, "
            "error_message FROM wrike_sync WHERE status IN ('pending', 'failed') "
            "ORDER BY recording_id"
        )
        return [WrikeSyncRow(*r) for r in cur.fetchall()]


class WrikeTaskRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, row: WrikeTaskRow) -> int:
        cur = self._db.conn.execute(
            "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
            "wrike_task_id, wrike_folder_id, created_at, last_synced_done) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row.recording_id, row.kind, row.todo_index, row.wrike_task_id,
             row.wrike_folder_id, row.created_at, 1 if row.last_synced_done else 0),
        )
        self._db.conn.commit()
        return cur.lastrowid

    def get(self, recording_id: int, kind: str, todo_index: int) -> WrikeTaskRow | None:
        cur = self._db.conn.execute(
            "SELECT id, recording_id, kind, todo_index, wrike_task_id, "
            "wrike_folder_id, created_at, last_synced_done "
            "FROM wrike_tasks WHERE recording_id=? AND kind=? AND todo_index=?",
            (recording_id, kind, todo_index),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return WrikeTaskRow(
            id=row[0], recording_id=row[1], kind=row[2], todo_index=row[3],
            wrike_task_id=row[4], wrike_folder_id=row[5], created_at=row[6],
            last_synced_done=bool(row[7]),
        )

    def list_for_recording(self, recording_id: int) -> list[WrikeTaskRow]:
        cur = self._db.conn.execute(
            "SELECT id, recording_id, kind, todo_index, wrike_task_id, "
            "wrike_folder_id, created_at, last_synced_done "
            "FROM wrike_tasks WHERE recording_id=? ORDER BY kind, todo_index",
            (recording_id,),
        )
        return [
            WrikeTaskRow(
                id=r[0], recording_id=r[1], kind=r[2], todo_index=r[3],
                wrike_task_id=r[4], wrike_folder_id=r[5], created_at=r[6],
                last_synced_done=bool(r[7]),
            )
            for r in cur.fetchall()
        ]

    def set_last_synced_done(
        self, recording_id: int, kind: str, todo_index: int, done: bool,
    ) -> None:
        self._db.conn.execute(
            "UPDATE wrike_tasks SET last_synced_done=? "
            "WHERE recording_id=? AND kind=? AND todo_index=?",
            (1 if done else 0, recording_id, kind, todo_index),
        )
        self._db.conn.commit()
```

Export from `storage/__init__.py`:
```python
from teams_transcriber.storage.wrike import (
    WrikeSyncRepo, WrikeSyncRow, WrikeTaskRepo, WrikeTaskRow,
)
```
Add them to `__all__`.

- [ ] **Step 8: Run repo tests to pass + full suite**

Run: `& "<uv>" run pytest tests/storage/test_wrike_repos.py -v`
Expected: PASS.
Run: `& "<uv>" run pytest -q`
Expected: all green (existing 351 + new tests).

- [ ] **Step 9: Commit**

```bash
git add src/teams_transcriber/storage/schema_v4.py src/teams_transcriber/storage/wrike.py src/teams_transcriber/storage/__init__.py tests/storage/test_schema_v4_migration.py tests/storage/test_wrike_repos.py
git commit -m "feat(storage): schema_v4 + Wrike repos (wrike_sync, wrike_tasks)"
```

---

## Task 2: Wrike REST client

**Files:**
- Create: `src/teams_transcriber/integrations/__init__.py` (empty)
- Create: `src/teams_transcriber/integrations/wrike_client.py`
- Test: `tests/integrations/__init__.py`, `tests/integrations/test_wrike_client.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integrations/test_wrike_client.py
import httpx
import pytest

from teams_transcriber.integrations.wrike_client import (
    WrikeApiError, WrikeAuthError, WrikeClient, WrikeRateLimitError,
)


def _client(handler) -> WrikeClient:
    transport = httpx.MockTransport(handler)
    return WrikeClient(token="tok", transport=transport)


def test_test_connection_returns_me_dict():
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/contacts/me")
        assert req.headers["Authorization"] == "bearer tok"
        return httpx.Response(200, json={"data": [{"id": "U1", "firstName": "Brian"}]})
    me = _client(h).test_connection()
    assert me["id"] == "U1"


def test_list_folders_returns_list():
    def h(req): return httpx.Response(200, json={"data": [
        {"id": "F1", "title": "Inbox"}, {"id": "F2", "title": "Meetings"},
    ]})
    out = _client(h).list_folders()
    assert [f["id"] for f in out] == ["F1", "F2"]


def test_list_contacts_returns_list():
    def h(req): return httpx.Response(200, json={"data": [
        {"id": "C1", "firstName": "Jennifer", "lastName": "Smith"},
    ]})
    out = _client(h).list_contacts()
    assert out[0]["firstName"] == "Jennifer"


def test_create_task_posts_to_folder():
    captured = {}
    def h(req):
        captured["url"] = str(req.url)
        captured["body"] = req.read().decode()
        return httpx.Response(200, json={"data": [{"id": "T1"}]})
    out = _client(h).create_task("F1", {"title": "Do thing"})
    assert out["id"] == "T1"
    assert "/folders/F1/tasks" in captured["url"]
    assert '"title": "Do thing"' in captured["body"]


def test_complete_task_puts_status():
    captured = {}
    def h(req):
        captured["method"] = req.method
        captured["body"] = req.read().decode()
        return httpx.Response(200, json={"data": [{"id": "T1", "status": "Completed"}]})
    _client(h).complete_task("T1", done=True)
    assert captured["method"] == "PUT"
    assert "Completed" in captured["body"]


def test_uncomplete_task_sets_active():
    captured = {}
    def h(req):
        captured["body"] = req.read().decode()
        return httpx.Response(200, json={"data": [{"id": "T1", "status": "Active"}]})
    _client(h).complete_task("T1", done=False)
    assert "Active" in captured["body"]


def test_auth_error_on_401():
    def h(req): return httpx.Response(401, json={"errorDescription": "bad token"})
    with pytest.raises(WrikeAuthError):
        _client(h).list_folders()


def test_rate_limit_retries_then_succeeds():
    calls = {"n": 0}
    def h(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"errorDescription": "throttled"})
        return httpx.Response(200, json={"data": [{"id": "F1", "title": "x"}]})
    out = _client(h).list_folders()
    assert calls["n"] == 2 and out[0]["id"] == "F1"


def test_rate_limit_gives_up_after_two_retries():
    def h(req): return httpx.Response(429, headers={"Retry-After": "0"})
    with pytest.raises(WrikeRateLimitError):
        _client(h).list_folders()


def test_other_5xx_raises_api_error():
    def h(req): return httpx.Response(500, text="boom")
    with pytest.raises(WrikeApiError):
        _client(h).list_folders()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& "<uv>" run pytest tests/integrations/test_wrike_client.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the client**

Create `src/teams_transcriber/integrations/__init__.py` (empty).
Create `src/teams_transcriber/integrations/wrike_client.py`:
```python
"""Wrike REST API client.

Permanent Access Token auth. Stateless: instantiate with a token + optional
custom transport (used by tests). All methods raise typed exceptions on
HTTP failure; the 429 path backs off with two retries before giving up.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

WRIKE_BASE_URL = "https://www.wrike.com/api/v4"
_MAX_RETRIES_ON_429 = 2
_DEFAULT_TIMEOUT_S = 30.0


class WrikeApiError(RuntimeError):
    """Generic Wrike API failure (non-auth, non-rate-limit)."""


class WrikeAuthError(WrikeApiError):
    """401/403 — token missing or invalid."""


class WrikeRateLimitError(WrikeApiError):
    """429 — exceeded retry budget."""


class WrikeClient:
    def __init__(
        self,
        *,
        token: str,
        base_url: str = WRIKE_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._token = token
        self._client = httpx.Client(
            base_url=base_url, transport=transport, timeout=timeout_s,
            headers={"Authorization": f"bearer {token}"},
        )

    # ---- public API -------------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        """Return the current user (first contact in /contacts/me's data list)."""
        data = self._request("GET", "/contacts/me")
        return data[0] if data else {}

    def list_folders(self) -> list[dict[str, Any]]:
        return self._request("GET", "/folders")

    def list_contacts(self) -> list[dict[str, Any]]:
        return self._request("GET", "/contacts")

    def create_task(self, folder_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", f"/folders/{folder_id}/tasks", json=payload)
        return data[0] if data else {}

    def complete_task(self, task_id: str, *, done: bool) -> dict[str, Any]:
        status = "Completed" if done else "Active"
        data = self._request("PUT", f"/tasks/{task_id}", json={"status": status})
        return data[0] if data else {}

    def close(self) -> None:
        self._client.close()

    # ---- internals --------------------------------------------------------

    def _request(self, method: str, path: str, *, json: Any | None = None) -> list[dict[str, Any]]:
        attempts = 0
        while True:
            attempts += 1
            resp = self._client.request(method, path, json=json)
            if resp.status_code == 429:
                if attempts > _MAX_RETRIES_ON_429:
                    raise WrikeRateLimitError(
                        f"Wrike rate-limited after {_MAX_RETRIES_ON_429} retries"
                    )
                # Respect Retry-After when present (test transports send 0).
                retry_after = float(resp.headers.get("Retry-After", "1"))
                logger.warning("Wrike 429; backing off %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code in (401, 403):
                raise WrikeAuthError(
                    f"Wrike auth failed ({resp.status_code}): "
                    f"{resp.json().get('errorDescription') if resp.headers.get('content-type','').startswith('application/json') else resp.text}"
                )
            if 500 <= resp.status_code < 600 or not resp.is_success:
                raise WrikeApiError(
                    f"Wrike {method} {path} -> {resp.status_code}: {resp.text[:200]}"
                )
            body = resp.json()
            data = body.get("data")
            return data if isinstance(data, list) else []
```

Create empty `tests/integrations/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `& "<uv>" run pytest tests/integrations/test_wrike_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/integrations/__init__.py src/teams_transcriber/integrations/wrike_client.py tests/integrations/__init__.py tests/integrations/test_wrike_client.py
git commit -m "feat(integrations): Wrike REST client with typed errors + 429 backoff"
```

---

## Task 3: Wrike sync orchestrator

**Files:**
- Create: `src/teams_transcriber/integrations/wrike_sync.py`
- Test: `tests/integrations/test_wrike_sync.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integrations/test_wrike_sync.py
import pytest

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import build_database
from teams_transcriber.storage.models import (
    ActionItemOther, Recording, RecordingSource, RecordingStatus, Summary, TodoItem,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.summaries import SummaryRepo
from teams_transcriber.storage.todos import TodoStateRepo
from teams_transcriber.storage.wrike import WrikeTaskRepo
from teams_transcriber.integrations.wrike_sync import sync_recording, SyncResult


class _FakeClient:
    """Captures create_task calls; returns predictable IDs."""

    def __init__(self, contacts=None) -> None:
        self.contacts = contacts or []
        self.created: list[tuple[str, dict]] = []   # (folder_id, payload)
        self._next = 1

    def test_connection(self): return {"id": "SELF"}
    def list_contacts(self): return self.contacts
    def create_task(self, folder_id, payload):
        tid = f"T{self._next}"; self._next += 1
        self.created.append((folder_id, payload))
        return {"id": tid}
    def complete_task(self, task_id, *, done): pass


@pytest.fixture
def env(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    yield paths, db
    db.close()


def _make_recording_with_summary(db, my_todos, others) -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-07T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="Potter Sync",
        display_title="Potter Sync", audio_path=None, audio_deleted_at=None,
        duration_ms=1500, status=RecordingStatus.DONE, error_message=None,
    ))
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="Potter Sync", one_line=None,
        summary="ok", key_decisions=[], my_todos=my_todos,
        action_items_others=others, follow_ups=[], topics=[],
        generated_at="2026-06-07T11:00:00+00:00", model_used="m",
    ))
    return rec.id


def test_sync_creates_tasks_for_my_and_others_and_persists_mappings(env):
    _, db = env
    rid = _make_recording_with_summary(
        db,
        my_todos=[TodoItem(task="Email Jennifer", due="2026-06-09"),
                  TodoItem(task="Order banner")],
        others=[ActionItemOther(who="Jennifer Smith", task="Send floor plan")],
    )
    client = _FakeClient(contacts=[
        {"id": "C_JEN", "firstName": "Jennifer", "lastName": "Smith"},
    ])
    res: SyncResult = sync_recording(db, client, rid, folder_id="F1")

    assert res.created_my == 2 and res.created_other == 1 and res.assigned_other == 1
    # 3 create_task calls total, all to F1.
    assert all(folder == "F1" for folder, _ in client.created)
    assert len(client.created) == 3
    # My-todo payloads tagged with self responsible, due forwarded.
    my_payloads = [p for (_, p) in client.created if p["title"] in ("Email Jennifer", "Order banner")]
    assert my_payloads[0]["responsibles"] == ["SELF"]
    assert any(p.get("dates", {}).get("due") == "2026-06-09" for p in my_payloads)
    # Other-task title prefixed and assigned to matched contact.
    other_payload = next(p for (_, p) in client.created if p["title"].startswith("For Jennifer Smith"))
    assert other_payload["responsibles"] == ["C_JEN"]
    # Mappings persisted.
    rows = WrikeTaskRepo(db).list_for_recording(rid)
    assert {r.kind for r in rows} == {"my", "other"}
    assert len(rows) == 3


def test_sync_is_idempotent_for_already_mapped_todos(env):
    _, db = env
    rid = _make_recording_with_summary(
        db, my_todos=[TodoItem(task="A")], others=[],
    )
    client = _FakeClient()
    sync_recording(db, client, rid, folder_id="F1")
    sync_recording(db, client, rid, folder_id="F1")   # second run: no-op
    assert len(client.created) == 1
    assert len(WrikeTaskRepo(db).list_for_recording(rid)) == 1


def test_sync_unassigns_when_contact_match_is_missing(env):
    _, db = env
    rid = _make_recording_with_summary(
        db, my_todos=[], others=[ActionItemOther(who="Unknown Person", task="X")],
    )
    client = _FakeClient(contacts=[
        {"id": "C_JEN", "firstName": "Jennifer", "lastName": "Smith"},
    ])
    res = sync_recording(db, client, rid, folder_id="F1")
    assert res.created_other == 1 and res.assigned_other == 0
    payload = client.created[0][1]
    assert "responsibles" not in payload or payload["responsibles"] == []


def test_sync_case_insensitive_exact_match_for_others(env):
    _, db = env
    rid = _make_recording_with_summary(
        db, my_todos=[], others=[ActionItemOther(who="jennifer smith", task="X")],
    )
    client = _FakeClient(contacts=[
        {"id": "C_JEN", "firstName": "Jennifer", "lastName": "Smith"},
    ])
    res = sync_recording(db, client, rid, folder_id="F1")
    assert res.assigned_other == 1
    assert client.created[0][1]["responsibles"] == ["C_JEN"]


def test_sync_ambiguous_match_does_not_assign(env):
    _, db = env
    rid = _make_recording_with_summary(
        db, my_todos=[], others=[ActionItemOther(who="John", task="X")],
    )
    client = _FakeClient(contacts=[
        {"id": "C_JOHN1", "firstName": "John", "lastName": "Doe"},
        {"id": "C_JOHN2", "firstName": "John", "lastName": "Smith"},
    ])
    res = sync_recording(db, client, rid, folder_id="F1")
    # "John" doesn't equal "John Doe" or "John Smith" full-name, so no match.
    assert res.assigned_other == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& "<uv>" run pytest tests/integrations/test_wrike_sync.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the sync orchestrator**

Create `src/teams_transcriber/integrations/wrike_sync.py`:
```python
"""Per-recording Wrike sync.

`sync_recording(db, client, recording_id, folder_id)` reads the Summary and
existing `wrike_tasks` mappings, creates one task per unmapped todo (mine +
others), and persists the new mappings. Idempotent: a second call with the
same recording is a no-op for already-mapped todos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.summaries import SummaryRepo
from teams_transcriber.storage.wrike import WrikeTaskRepo, WrikeTaskRow

logger = logging.getLogger(__name__)


class _ClientProto(Protocol):
    def test_connection(self) -> dict[str, Any]: ...
    def list_contacts(self) -> list[dict[str, Any]]: ...
    def create_task(self, folder_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def complete_task(self, task_id: str, *, done: bool) -> dict[str, Any]: ...


@dataclass(slots=True)
class SyncResult:
    created_my: int = 0
    created_other: int = 0
    assigned_other: int = 0   # how many of the others got a Wrike contact match


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _full_name(contact: dict[str, Any]) -> str:
    return f"{contact.get('firstName','').strip()} {contact.get('lastName','').strip()}".strip()


def _match_contact(name: str, contacts: list[dict[str, Any]]) -> str | None:
    """Case-insensitive exact full-name match. Returns contact id, or None for
    no match / ambiguous match."""
    needle = name.strip().lower()
    hits = [c for c in contacts if _full_name(c).lower() == needle]
    if len(hits) == 1:
        return hits[0]["id"]
    return None


def sync_recording(
    db: Database,
    client: _ClientProto,
    recording_id: int,
    *,
    folder_id: str,
) -> SyncResult:
    """Create Wrike tasks for any todos on `recording_id` not already mapped."""
    summary = SummaryRepo(db).get(recording_id)
    if summary is None:
        return SyncResult()
    rec = RecordingRepo(db).get(recording_id)
    rec_title = (rec.display_title if rec else None) or summary.title or "Meeting"
    started_at = (rec.started_at if rec else "")[:10]   # YYYY-MM-DD-ish for header

    task_repo = WrikeTaskRepo(db)
    existing = {(r.kind, r.todo_index) for r in task_repo.list_for_recording(recording_id)}
    res = SyncResult()

    # Resolve self contact id (used for my-todo assignments). Cheap: cached via me probe.
    self_id = client.test_connection().get("id")

    # My todos.
    for i, td in enumerate(summary.my_todos):
        if ("my", i) in existing:
            continue
        payload = {
            "title": td.task,
            "description": _build_description(rec_title, started_at, td.context),
            "status": "Active",
        }
        if td.due:
            payload["dates"] = {"due": td.due}
        if self_id:
            payload["responsibles"] = [self_id]
        created = client.create_task(folder_id, payload)
        task_repo.insert(WrikeTaskRow(
            id=None, recording_id=recording_id, kind="my", todo_index=i,
            wrike_task_id=str(created["id"]), wrike_folder_id=folder_id,
            created_at=_now_iso(), last_synced_done=False,
        ))
        res.created_my += 1

    # Action items for others.
    if summary.action_items_others:
        contacts = client.list_contacts()
    else:
        contacts = []
    for j, ai in enumerate(summary.action_items_others):
        if ("other", j) in existing:
            continue
        matched_id = _match_contact(ai.who, contacts)
        payload = {
            "title": f"For {ai.who}: {ai.task}",
            "description": _build_description(rec_title, started_at, None),
            "status": "Active",
        }
        if ai.due:
            payload["dates"] = {"due": ai.due}
        if matched_id:
            payload["responsibles"] = [matched_id]
            res.assigned_other += 1
        created = client.create_task(folder_id, payload)
        task_repo.insert(WrikeTaskRow(
            id=None, recording_id=recording_id, kind="other", todo_index=j,
            wrike_task_id=str(created["id"]), wrike_folder_id=folder_id,
            created_at=_now_iso(), last_synced_done=False,
        ))
        res.created_other += 1

    return res


def _build_description(meeting_title: str, started_at: str, context: str | None) -> str:
    parts = [f"From meeting: {meeting_title} ({started_at})"]
    if context:
        parts.append(context)
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass + full suite**

Run: `& "<uv>" run pytest tests/integrations/ tests/storage/ -v`
Expected: PASS.
Run: `& "<uv>" run pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/integrations/wrike_sync.py tests/integrations/test_wrike_sync.py
git commit -m "feat(integrations): per-recording Wrike sync (idempotent, contact-match)"
```

---

## Task 4: Settings — Integrations tab

**Files:**
- Modify: `src/teams_transcriber/config.py` (new keys + keyring constant)
- Modify: `src/teams_transcriber/ui/settings_dialog.py` (new tab + handlers)
- Test: `tests/ui/test_settings_integrations_tab.py`

- [ ] **Step 1: Add the failing test**

```python
# tests/ui/test_settings_integrations_tab.py
from pathlib import Path
from types import SimpleNamespace

import pytest

from teams_transcriber.config import load_settings
from teams_transcriber.paths import AppPaths
from teams_transcriber.ui.settings_dialog import SettingsDialog


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT"); p.ensure_dirs()
    return p


def test_integrations_tab_present_with_token_and_enable(qapp, paths):
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    titles = [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())]
    assert "Integrations" in titles
    assert dlg.wrike_token_input is not None
    assert dlg.wrike_enable_cb is not None
    # Default disabled.
    assert dlg.wrike_enable_cb.isChecked() is False


def test_test_connection_updates_label_on_success(qapp, paths, monkeypatch):
    from teams_transcriber.integrations import wrike_client
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg.wrike_token_input.setText("tok")
    # Stub WrikeClient.test_connection by replacing the class with a fake.
    class _FakeClient:
        def __init__(self, *, token, **_): pass
        def test_connection(self): return {"id": "U1", "firstName": "Brian"}
        def close(self): pass
    monkeypatch.setattr(wrike_client, "WrikeClient", _FakeClient)
    dlg._wrike_test_connection()
    assert "Brian" in dlg.wrike_status_label.text()


def test_test_connection_shows_error_on_auth_failure(qapp, paths, monkeypatch):
    from teams_transcriber.integrations import wrike_client
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg.wrike_token_input.setText("tok")
    class _FakeClient:
        def __init__(self, *, token, **_): pass
        def test_connection(self):
            from teams_transcriber.integrations.wrike_client import WrikeAuthError
            raise WrikeAuthError("bad token")
        def close(self): pass
    monkeypatch.setattr(wrike_client, "WrikeClient", _FakeClient)
    dlg._wrike_test_connection()
    assert "bad token" in dlg.wrike_status_label.text().lower() or "failed" in dlg.wrike_status_label.text().lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& "<uv>" run pytest tests/ui/test_settings_integrations_tab.py -v`
Expected: FAIL — tab/widgets missing.

- [ ] **Step 3: Implement config + tab**

In `src/teams_transcriber/config.py`, add (near the other `KEYRING_USER_*` constants):
```python
KEYRING_USER_WRIKE = "wrike_api_token"
```
Add to `Settings` (or its `_raw["integrations"]` defaults):
- `integrations.wrike_enabled: bool` default `False`
- `integrations.wrike_recent_folder_ids: list[str]` default `[]`

(Add exact default-merge logic mirroring how `general.auto_check_updates` was added in Phase 8 — read `_raw.setdefault("integrations", {}).setdefault("wrike_enabled", False)` in `load_settings`, and properties on `Settings` if other code reads them.)

In `src/teams_transcriber/ui/settings_dialog.py`:
- Add a new tab between AI and Shortcuts:
```python
self._tabs.addTab(self._build_integrations_tab(), "Integrations")
```
(insert AFTER the AI tab line and BEFORE the Shortcuts line).
- Implement `_build_integrations_tab`:
```python
    def _build_integrations_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        # Token (keyring-backed).
        self.wrike_token_input = QLineEdit()
        self.wrike_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        existing = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
        self.wrike_token_input.setText(existing)
        form.addRow("Wrike API token:", self.wrike_token_input)

        # Test connection.
        test_btn = QPushButton("Test connection")
        test_btn.setProperty("role", "secondary")
        test_btn.clicked.connect(self._wrike_test_connection)
        self.wrike_status_label = QLabel("")
        self.wrike_status_label.setWordWrap(True)
        form.addRow("", test_btn)
        form.addRow("", self.wrike_status_label)

        # Enable.
        self.wrike_enable_cb = QCheckBox(
            "Send meeting todos to Wrike automatically when a summary is ready"
        )
        self.wrike_enable_cb.setChecked(
            bool(self._settings._raw.get("integrations", {}).get("wrike_enabled", False))
        )
        form.addRow("", self.wrike_enable_cb)
        return w
```
- Add the handler:
```python
    def _wrike_test_connection(self) -> None:
        from teams_transcriber.integrations import wrike_client as _wc
        token = self.wrike_token_input.text().strip()
        if not token:
            self.wrike_status_label.setText("Enter a token first.")
            return
        self.wrike_status_label.setText("Checking…")
        QApplication.processEvents()
        try:
            client = _wc.WrikeClient(token=token)
            me = client.test_connection()
            client.close()
            name = (me.get("firstName") or "user") + " " + (me.get("lastName") or "")
            self.wrike_status_label.setText(
                f"<span style='color:#065F46;'>✓ Connected as {name.strip()}</span>"
            )
        except Exception as exc:
            self.wrike_status_label.setText(
                f"<span style='color:#DC2626;'>✗ {exc}</span>"
            )
```
- In `_on_accept`, persist the new fields:
```python
        # Wrike token to keyring.
        new_token = self.wrike_token_input.text().strip()
        if new_token:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER_WRIKE, new_token)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_USER_WRIKE)
            except keyring.errors.PasswordDeleteError:
                pass
        # Wrike enabled flag.
        self._settings._raw.setdefault("integrations", {})["wrike_enabled"] = (
            self.wrike_enable_cb.isChecked()
        )
```

- [ ] **Step 4: Run tests to verify they pass + import smoke**

Run: `& "<uv>" run pytest tests/ui/test_settings_integrations_tab.py -v`
Expected: PASS.
Run: `& "<uv>" run python -c "import teams_transcriber.ui.settings_dialog; print('OK')"`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/config.py src/teams_transcriber/ui/settings_dialog.py tests/ui/test_settings_integrations_tab.py
git commit -m "feat(settings): Integrations tab with Wrike token + Test connection"
```

---

## Task 5: Folder picker dialog

**Files:**
- Create: `src/teams_transcriber/ui/wrike_folder_picker.py`
- Test: `tests/ui/test_wrike_folder_picker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_wrike_folder_picker.py
from PySide6.QtCore import Qt
from teams_transcriber.ui.wrike_folder_picker import WrikeFolderPicker


def _folders():
    return [
        {"id": "F1", "title": "Inbox"},
        {"id": "F2", "title": "Meetings"},
        {"id": "F3", "title": "Personal"},
    ]


def test_picker_lists_recent_first_then_rest(qapp):
    dlg = WrikeFolderPicker(
        folders=_folders(), recent_folder_ids=["F2"],
    )
    items = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    assert items[0].startswith("Meetings")    # recent first
    assert "Inbox" in items[1] or "Inbox" in items[2]


def test_picker_search_filters_visible_rows(qapp):
    dlg = WrikeFolderPicker(folders=_folders(), recent_folder_ids=[])
    dlg._search.setText("meet")
    visible = [dlg._list.item(i).text()
               for i in range(dlg._list.count())
               if not dlg._list.item(i).isHidden()]
    assert visible == [v for v in visible if "Meetings" in v]


def test_picker_returns_selected_folder_id(qapp):
    dlg = WrikeFolderPicker(folders=_folders(), recent_folder_ids=[])
    dlg._list.setCurrentRow(0)
    dlg._on_accept()
    assert dlg.selected_folder_id in {"F1", "F2", "F3"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& "<uv>" run pytest tests/ui/test_wrike_folder_picker.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the picker**

Create `src/teams_transcriber/ui/wrike_folder_picker.py`:
```python
"""Themed modal dialog: pick a Wrike folder for a summary's tasks."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.title_bar import TitleBar


class WrikeFolderPicker(FramelessWindowMixin, QDialog):
    """List recent + all folders, with a search box. Returns selected id."""

    def __init__(
        self,
        *,
        folders: list[dict[str, Any]],
        recent_folder_ids: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick Wrike folder")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(420, 460)
        self.selected_folder_id: str | None = None

        frame = QFrame(); frame.setObjectName("OuterFrame")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0); shell.addWidget(frame)
        inner = QVBoxLayout(frame); inner.setContentsMargins(0, 0, 0, 0); inner.setSpacing(0)

        self._title_bar = TitleBar(title="Pick Wrike folder", controls=("close",))
        self._title_bar.close_requested.connect(self.reject)
        inner.addWidget(self._title_bar)

        body = QWidget()
        v = QVBoxLayout(body); v.setContentsMargins(16, 12, 16, 16); v.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search folders…")
        self._search.textChanged.connect(self._apply_filter)
        v.addWidget(self._search)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _i: self._on_accept())
        v.addWidget(self._list, 1)

        # Populate: recent first, then the rest (deduped).
        recent_set = set(recent_folder_ids)
        ordered = (
            [f for fid in recent_folder_ids for f in folders if f["id"] == fid]
            + [f for f in folders if f["id"] not in recent_set]
        )
        for i, f in enumerate(ordered):
            item = QListWidgetItem(f["title"])
            item.setData(Qt.ItemDataRole.UserRole, f["id"])
            if f["id"] in recent_set:
                item.setText(f"{f['title']}  ★")   # mark recent
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

        btn_row = QHBoxLayout(); btn_row.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.setProperty("role", "secondary")
        cancel.clicked.connect(self.reject); btn_row.addWidget(cancel)
        ok = QPushButton("Send"); ok.setProperty("role", "primary"); ok.setDefault(True)
        ok.clicked.connect(self._on_accept); btn_row.addWidget(ok)
        v.addLayout(btn_row)

        inner.addWidget(body, 1)
        self._init_frameless(frame, resizable=True, title_bar=self._title_bar)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _on_accept(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        self.selected_folder_id = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `& "<uv>" run pytest tests/ui/test_wrike_folder_picker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/wrike_folder_picker.py tests/ui/test_wrike_folder_picker.py
git commit -m "feat(ui): WrikeFolderPicker dialog (recent + search)"
```

---

## Task 6: App wiring — SummaryReady → toast/picker → sync

**Files:**
- Modify: `src/teams_transcriber/ui/app.py`
- Test: `tests/ui/test_app_wrike_wiring.py`

- [ ] **Step 1: Write the failing test (predicate-level — no full App)**

```python
# tests/ui/test_app_wrike_wiring.py
# Factor the predicate and orchestration into small testable units so we can
# verify wiring without constructing a full App. See Step 3.

def test_should_offer_wrike_sync_predicate():
    from teams_transcriber.ui.app import _wrike_should_offer_sync
    # No setting => skip.
    assert _wrike_should_offer_sync(enabled=False, has_token=True, already_synced=False) is False
    # No token => skip.
    assert _wrike_should_offer_sync(enabled=True, has_token=False, already_synced=False) is False
    # Already synced => skip.
    assert _wrike_should_offer_sync(enabled=True, has_token=True, already_synced=True) is False
    # All clear => offer.
    assert _wrike_should_offer_sync(enabled=True, has_token=True, already_synced=False) is True


def test_lru_recent_folder_ids():
    from teams_transcriber.ui.app import _wrike_lru_push
    assert _wrike_lru_push([], "F1", cap=5) == ["F1"]
    # Moving an existing id to the front is idempotent in length.
    assert _wrike_lru_push(["A", "B", "C"], "B", cap=5) == ["B", "A", "C"]
    # Cap is respected.
    assert _wrike_lru_push(["A", "B", "C", "D", "E"], "F", cap=5) == ["F", "A", "B", "C", "D"]
```

- [ ] **Step 2: Run to verify failure**

Run: `& "<uv>" run pytest tests/ui/test_app_wrike_wiring.py -v`
Expected: FAIL — helpers undefined.

- [ ] **Step 3: Implement the predicate + LRU + wiring**

In `src/teams_transcriber/ui/app.py`, add module-level helpers (near
`_default_export_name`):
```python
def _wrike_should_offer_sync(
    *, enabled: bool, has_token: bool, already_synced: bool,
) -> bool:
    return enabled and has_token and not already_synced


def _wrike_lru_push(items: list[str], value: str, *, cap: int) -> list[str]:
    rest = [i for i in items if i != value]
    return ([value] + rest)[:cap]
```

In `App.__init__` near the other bridge connections:
```python
self.bridge.summary_ready.connect(self._on_summary_ready_wrike)
```

(`summary_ready` is already an existing Qt-bridge signal from Phase 8/9 — verify by grepping `bridge.summary_ready`. Do NOT duplicate an existing connection.)

Add the handler near `_on_summary_ready` (the existing notes-and-toast one):
```python
    def _on_summary_ready_wrike(self, evt) -> None:
        from teams_transcriber.storage import SummaryRepo
        from teams_transcriber.storage.wrike import WrikeSyncRepo
        import keyring
        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE
        token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
        enabled = bool(self.settings._raw.get("integrations", {}).get("wrike_enabled", False))
        already = WrikeSyncRepo(self.db).get(evt.recording_id)
        already_synced = bool(already and already.status == "synced")
        if not _wrike_should_offer_sync(
            enabled=enabled, has_token=bool(token), already_synced=already_synced,
        ):
            return
        s = SummaryRepo(self.db).get(evt.recording_id)
        if s is None:
            return
        n = len(s.my_todos) + len(s.action_items_others)
        if n == 0:
            return
        WrikeSyncRepo(self.db).upsert(evt.recording_id, status="pending")
        show_in_app_toast(
            "Send todos to Wrike",
            f"{n} task{'s' if n != 1 else ''} ready — pick a folder.",
            action_label="Pick folder",
            action_callback=lambda rid=evt.recording_id: self._wrike_open_picker(rid),
        )

    def _wrike_open_picker(self, recording_id: int) -> None:
        import keyring
        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE
        from teams_transcriber.integrations.wrike_client import WrikeClient, WrikeApiError
        from teams_transcriber.ui.wrike_folder_picker import WrikeFolderPicker
        from teams_transcriber.storage.wrike import WrikeSyncRepo

        token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
        if not token:
            show_in_app_toast("Wrike not configured", "Add a token in Settings → Integrations.")
            return
        client = WrikeClient(token=token)
        try:
            folders = client.list_folders()
        except WrikeApiError as exc:
            client.close()
            show_in_app_toast("Wrike error", str(exc))
            WrikeSyncRepo(self.db).update(recording_id, status="failed", error_message=str(exc))
            return
        client.close()
        recent_ids = self.settings._raw.get("integrations", {}).get("wrike_recent_folder_ids", []) or []
        dlg = WrikeFolderPicker(
            folders=folders, recent_folder_ids=list(recent_ids), parent=self.window,
        )
        if dlg.exec() != dlg.DialogCode.Accepted or not dlg.selected_folder_id:
            return
        folder_id = dlg.selected_folder_id
        new_recent = _wrike_lru_push(list(recent_ids), folder_id, cap=5)
        self.settings._raw.setdefault("integrations", {})["wrike_recent_folder_ids"] = new_recent
        from teams_transcriber.config import save_settings
        save_settings(self.paths, self.settings)
        threading.Thread(
            target=self._wrike_run_sync, args=(recording_id, folder_id, token),
            daemon=True,
        ).start()

    def _wrike_run_sync(self, recording_id: int, folder_id: str, token: str) -> None:
        from teams_transcriber.integrations.wrike_client import WrikeClient, WrikeApiError
        from teams_transcriber.integrations.wrike_sync import sync_recording
        from teams_transcriber.storage.wrike import WrikeSyncRepo
        client = WrikeClient(token=token)
        try:
            result = sync_recording(self.db, client, recording_id, folder_id=folder_id)
            WrikeSyncRepo(self.db).update(recording_id, status="synced", folder_id=folder_id)
            n = result.created_my + result.created_other
            show_in_app_toast(
                "Synced to Wrike",
                f"Created {n} task{'s' if n != 1 else ''}"
                + (f" — {result.assigned_other} assigned"
                   if result.assigned_other else ""),
            )
        except WrikeApiError as exc:
            WrikeSyncRepo(self.db).update(recording_id, status="failed", error_message=str(exc))
            show_in_app_toast("Wrike sync failed", str(exc))
        finally:
            client.close()
```

- [ ] **Step 4: Run tests + import smoke + full suite**

Run: `& "<uv>" run pytest tests/ui/test_app_wrike_wiring.py -v`
Expected: PASS.
Run: `& "<uv>" run python -c "import teams_transcriber.ui.app; print('OK')"`
Expected: OK.
Run: `& "<uv>" run pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py tests/ui/test_app_wrike_wiring.py
git commit -m "feat(app): toast-driven Wrike folder picker on SummaryReady"
```

---

## Task 7: Close-loop on todo toggle

**Files:**
- Modify: `src/teams_transcriber/ui/app.py` (extend `_on_todo_state_changed`)
- Test: `tests/ui/test_app_wrike_close_loop.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_app_wrike_close_loop.py
from teams_transcriber.ui.app import _wrike_close_loop_changes
from teams_transcriber.storage.wrike import WrikeTaskRow


def test_close_loop_only_returns_changed_rows():
    rows = [
        WrikeTaskRow(id=1, recording_id=10, kind="my", todo_index=0,
                     wrike_task_id="T1", wrike_folder_id="F1",
                     created_at="x", last_synced_done=False),
        WrikeTaskRow(id=2, recording_id=10, kind="my", todo_index=1,
                     wrike_task_id="T2", wrike_folder_id="F1",
                     created_at="x", last_synced_done=True),
    ]
    todo_states = {0: True, 1: True}    # idx0 changed from False, idx1 unchanged
    changes = _wrike_close_loop_changes(rows, todo_states)
    assert len(changes) == 1
    row, new_done = changes[0]
    assert row.wrike_task_id == "T1" and new_done is True


def test_close_loop_ignores_other_kind():
    rows = [
        WrikeTaskRow(id=1, recording_id=10, kind="other", todo_index=0,
                     wrike_task_id="T1", wrike_folder_id="F1",
                     created_at="x", last_synced_done=False),
    ]
    changes = _wrike_close_loop_changes(rows, todo_states={0: True})
    assert changes == []     # action-items-for-others not toggleable in app
```

- [ ] **Step 2: Run to verify failure**

Run: `& "<uv>" run pytest tests/ui/test_app_wrike_close_loop.py -v`
Expected: FAIL — helper undefined.

- [ ] **Step 3: Implement the helper + wire it**

Add to `app.py` (module level, near the other `_wrike_*` helpers):
```python
def _wrike_close_loop_changes(
    rows: list,    # list[WrikeTaskRow]
    todo_states: dict[int, bool],
) -> list[tuple]:    # list[(WrikeTaskRow, new_done)]
    out: list[tuple] = []
    for r in rows:
        if r.kind != "my":
            continue
        new_done = bool(todo_states.get(r.todo_index, False))
        if new_done != r.last_synced_done:
            out.append((r, new_done))
    return out
```

Extend the existing `App._on_todo_state_changed` (Phase 10):
```python
    def _on_todo_state_changed(self, rid: int) -> None:
        self._refresh_history(query=self.search.input.text() or None)
        self.master_todos.reload()
        # Wrike close-loop (one-way app -> Wrike).
        self._wrike_close_loop_sync(rid)

    def _wrike_close_loop_sync(self, recording_id: int) -> None:
        import keyring
        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE
        from teams_transcriber.storage import TodoStateRepo
        from teams_transcriber.storage.wrike import WrikeTaskRepo
        token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
        enabled = bool(self.settings._raw.get("integrations", {}).get("wrike_enabled", False))
        if not (enabled and token):
            return
        rows = WrikeTaskRepo(self.db).list_for_recording(recording_id)
        if not rows:
            return
        todo_states = {
            s.todo_index: s.done
            for s in TodoStateRepo(self.db).list_for_recording(recording_id)
        }
        changes = _wrike_close_loop_changes(rows, todo_states)
        if not changes:
            return
        threading.Thread(
            target=self._wrike_apply_close_loop, args=(recording_id, changes, token),
            daemon=True,
        ).start()

    def _wrike_apply_close_loop(
        self, recording_id: int, changes: list, token: str,
    ) -> None:
        from teams_transcriber.integrations.wrike_client import WrikeClient, WrikeApiError
        from teams_transcriber.storage.wrike import WrikeTaskRepo
        client = WrikeClient(token=token)
        repo = WrikeTaskRepo(self.db)
        try:
            for row, new_done in changes:
                try:
                    client.complete_task(row.wrike_task_id, done=new_done)
                    repo.set_last_synced_done(
                        recording_id, row.kind, row.todo_index, new_done,
                    )
                except WrikeApiError as exc:
                    logger.warning("Wrike close-loop failed for %s: %s",
                                   row.wrike_task_id, exc)
        finally:
            client.close()
```

- [ ] **Step 4: Run tests + import smoke**

Run: `& "<uv>" run pytest tests/ui/test_app_wrike_close_loop.py -v`
Expected: PASS.
Run: `& "<uv>" run python -c "import teams_transcriber.ui.app; print('OK')"`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py tests/ui/test_app_wrike_close_loop.py
git commit -m "feat(app): Wrike close-loop on todo toggle (app -> Wrike one-way)"
```

---

## Task 8: Pending-syncs retry on startup

**Files:**
- Modify: `src/teams_transcriber/ui/app.py` (call at end of `__init__`)
- Test: `tests/ui/test_app_wrike_pending.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_app_wrike_pending.py
def test_pending_retry_picks_oldest_first():
    from teams_transcriber.ui.app import _wrike_pick_pending
    from teams_transcriber.storage.wrike import WrikeSyncRow
    rows = [
        WrikeSyncRow(recording_id=3, folder_id=None, status="pending",
                     last_attempted_at="2026-06-01", error_message=None),
        WrikeSyncRow(recording_id=1, folder_id=None, status="failed",
                     last_attempted_at="2026-05-30", error_message="boom"),
        WrikeSyncRow(recording_id=2, folder_id="F", status="synced",
                     last_attempted_at="2026-06-02", error_message=None),
    ]
    rid = _wrike_pick_pending(rows)
    assert rid == 1   # earliest last_attempted_at among pending/failed
```

- [ ] **Step 2: Run to verify failure**

Run: `& "<uv>" run pytest tests/ui/test_app_wrike_pending.py -v`
Expected: FAIL — helper undefined.

- [ ] **Step 3: Implement helper + wire**

Add to `app.py`:
```python
def _wrike_pick_pending(rows: list) -> int | None:
    pending = [r for r in rows if r.status in ("pending", "failed")]
    if not pending:
        return None
    pending.sort(key=lambda r: r.last_attempted_at or "")
    return pending[0].recording_id
```

In `App.__init__`, AFTER `self._refresh_history()` and before the update check:
```python
        # Offer to retry any pending/failed Wrike syncs (consolidated toast).
        try:
            from teams_transcriber.storage.wrike import WrikeSyncRepo
            pending = WrikeSyncRepo(self.db).list_pending_or_failed()
            rid = _wrike_pick_pending(pending)
            if rid is not None:
                count = len(pending)
                show_in_app_toast(
                    "Pending Wrike syncs",
                    f"{count} meeting{'s' if count != 1 else ''} waiting.",
                    action_label="Pick folder",
                    action_callback=lambda r=rid: self._wrike_open_picker(r),
                )
        except Exception:
            logger.exception("pending-Wrike-syncs check failed")
```

- [ ] **Step 4: Run + import smoke + full suite**

Run: `& "<uv>" run pytest tests/ui/test_app_wrike_pending.py -v`
Expected: PASS.
Run: `& "<uv>" run python -c "import teams_transcriber.ui.app; print('OK')"`
Expected: OK.
Run: `& "<uv>" run pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py tests/ui/test_app_wrike_pending.py
git commit -m "feat(app): startup pending-Wrike-syncs toast (oldest first)"
```

---

## Final verification

- [ ] **Full suite green**: `& "<uv>" run pytest -q` — all passing.
- [ ] **Manual smoke**:
  - Settings → Integrations → paste a real Wrike PAT → Test connection → ✓ Connected as <you> → check "Send meeting todos to Wrike automatically" → Save.
  - Run a meeting; on summary, see toast → Pick folder → folder picker → Send → tasks appear in the chosen Wrike folder. Verify due dates + assignment to you + "For Jennifer: ..." prefix for matched-or-unmatched others.
  - Toggle a my-todo checkbox → confirm the Wrike task flips Completed / Active.
  - Revoke token in Wrike → trigger another summary → confirm failed toast and the pending-sync toast on next launch.
- [ ] **Memory + spec status**:
  - Update `memory/project_teams_transcriber.md` with a Phase 11 summary.
  - Mark the spec Status line.
  - Invoke `superpowers:finishing-a-development-branch`.

---

## Self-review notes (author)

- Spec coverage:
  - Permanent token + Settings + keyring → Task 4.
  - Auto-sync on `SummaryReady` + toast/picker → Task 6.
  - "Pick per meeting" via picker → Task 5 + Task 6.
  - Action items for others + contact matching → Task 3 (`_match_contact`).
  - Close-loop on todo toggle → Task 7.
  - Pending retry on startup → Task 8.
  - Schema additions → Task 1.
  - Wrike client + 429 backoff → Task 2.
- Type/name consistency:
  - `WrikeSyncRow` / `WrikeTaskRow` field names match across repos, sync, app.
  - `sync_recording(db, client, recording_id, *, folder_id) -> SyncResult` used uniformly.
  - `_wrike_should_offer_sync(*, enabled, has_token, already_synced)` predicate
    used in Task 6 with named args matching the test.
  - `_wrike_close_loop_changes(rows, todo_states)` and `_wrike_pick_pending(rows)`
    signatures match their tests.
- Placeholders: none. Where a step instructs "verify by grepping" (the existing
  `bridge.summary_ready` connection), that's a concrete instruction, not a TBD.
