# Phone Sync Engine (Android Companion Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The desktop side of the Android companion's sync contract — pull phone recordings into the existing transcribe/summarize pipeline, apply phone-side todo toggles (last-write-wins), export the full library back, and acknowledge — all against a transport interface whose `LocalDirTransport` implementation makes the engine fully testable and immediately usable with any folder-sync tool.

**Architecture:** New `src/teams_transcriber/phone_sync/` package (contract parsing, transport interface, library export, sync orchestrator) plus a `phone_imports` UID ledger table (schema v7) and a metadata-aware import path. No UI in this phase — Phase 2 adds the MTP transport, device watcher, and settings; Phases 3-4 are the Android app. Spec: `docs/superpowers/specs/2026-07-14-android-companion-design.md`.

**Tech Stack:** Python 3.11, stdlib json/dataclasses, SQLite via existing `storage` layer, pytest.

## Global Constraints

- uv-only tooling (`uv run pytest`); if `uv` isn't on the sandbox PATH use `.venv\Scripts\python.exe -m pytest`. Use the Bash tool for git (missing from PowerShell PATH).
- Sync-contract values from the spec, verbatim: contract `SCHEMA_VERSION = 1`; phone folder layout `outbox/`, `library/`, `sync/desktop_ack.json`; sidecar `source ∈ {"teams_call", "in_person", "memo"}`; recordings are `rec_<uuid>.m4a` + `rec_<uuid>.json` sidecars.
- Rules from the spec, verbatim: idempotency via a UID ledger; an outbox file is deleted from the phone only after its import is committed; todo conflicts are last-write-wins (`toggled_at` vs `done_at`); single-writer per file (desktop never edits `changes.json`; it only reads it and reports `changes_applied_through` in the ack).
- Desktop `recordings.source` stays `MANUAL` for phone imports (no CHECK-constraint migration — same decision as `audio/importer.py`); the phone-side source lives in the `phone_imports` ledger and overrides `source` in the library export.
- Applied toggles must trigger the Wrike close-loop — via an `on_todos_changed(recording_id)` callback the caller wires (App wires `_wrike_close_loop_sync` in Phase 2; the CLI passes None).
- Conventional commits. Every commit message ends with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_018PYRS2ZXeA6Z4ra2xkRsDz`
- Run the full suite before each commit; ~538 existing tests must stay green.

---

### Task 1: `phone_imports` ledger — schema v7 + repo

**Files:**
- Create: `src/teams_transcriber/storage/schema_v7.py`
- Create: `src/teams_transcriber/storage/phone.py`
- Modify: `src/teams_transcriber/storage/__init__.py` (import + register after `SCHEMA_V6`, export repo)
- Test: `tests/storage/test_phone_imports.py`

**Interfaces:**
- Consumes: `Migration` from `storage/migrations.py`; the migration list in `storage/__init__.py::build_database` (append `SCHEMA_V7` after `SCHEMA_V6`).
- Produces: `PhoneImportRepo(db)` with `record(uid: str, recording_id: int, source: str) -> None`, `recording_id_for(uid: str) -> int | None`, `source_for_recordings() -> dict[int, str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/storage/test_phone_imports.py` (the `db` fixture from `tests/conftest.py` applies all registered migrations):

```python
from __future__ import annotations

from teams_transcriber.storage import (
    PhoneImportRepo, Recording, RecordingRepo, RecordingSource, RecordingStatus,
)


def _make_recording(db) -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-07-14T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    return rec.id


def test_record_and_lookup_roundtrip(db):
    rid = _make_recording(db)
    repo = PhoneImportRepo(db)
    assert repo.recording_id_for("uid-1") is None
    repo.record("uid-1", rid, "in_person")
    assert repo.recording_id_for("uid-1") == rid
    assert repo.source_for_recordings() == {rid: "in_person"}


def test_record_same_uid_twice_is_noop(db):
    rid = _make_recording(db)
    repo = PhoneImportRepo(db)
    repo.record("uid-1", rid, "memo")
    repo.record("uid-1", rid, "memo")   # idempotent, no IntegrityError
    assert repo.recording_id_for("uid-1") == rid


def test_ledger_row_cascades_with_recording(db):
    rid = _make_recording(db)
    repo = PhoneImportRepo(db)
    repo.record("uid-1", rid, "teams_call")
    RecordingRepo(db).delete(rid)
    assert repo.recording_id_for("uid-1") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/storage/test_phone_imports.py -v`
Expected: FAIL with `ImportError: cannot import name 'PhoneImportRepo'`.

- [ ] **Step 3: Implement**

`src/teams_transcriber/storage/schema_v7.py`:

```python
"""v7: phone_imports — UID ledger for recordings imported from the Android app."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS = (
    """
    CREATE TABLE phone_imports (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        uid           TEXT NOT NULL UNIQUE,
        recording_id  INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        source        TEXT NOT NULL,
        imported_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_phone_imports_recording ON phone_imports(recording_id)",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V7 = Migration(version=7, name="add phone_imports ledger", apply=_apply)
```

`src/teams_transcriber/storage/phone.py`:

```python
"""Repo for phone_imports — maps Android-app recording UIDs to recording ids.

The ledger makes phone sync idempotent (a re-pulled outbox file whose uid is
already recorded is skipped) and carries the phone-side source
(teams_call | in_person | memo), which recordings.source cannot hold.
"""

from __future__ import annotations

from datetime import UTC, datetime

from teams_transcriber.storage.db import Database


class PhoneImportRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, uid: str, recording_id: int, source: str) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO phone_imports (uid, recording_id, source, imported_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(uid) DO NOTHING",
                (uid, recording_id, source, datetime.now(UTC).isoformat()),
            )
            conn.commit()

    def recording_id_for(self, uid: str) -> int | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT recording_id FROM phone_imports WHERE uid = ?", (uid,),
            ).fetchone()
        return row[0] if row is not None else None

    def source_for_recordings(self) -> dict[int, str]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT recording_id, source FROM phone_imports",
            ).fetchall()
        return {r[0]: r[1] for r in rows}
```

`storage/__init__.py`: add `from teams_transcriber.storage.phone import PhoneImportRepo` and `from teams_transcriber.storage.schema_v7 import SCHEMA_V7` in the alphabetical import blocks; append `SCHEMA_V7` after `SCHEMA_V6` in `build_database`'s migrations list; add both names to `__all__` if the module defines one.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/storage/test_phone_imports.py tests/storage -v` then the full suite once.
Expected: PASS (migration ordering check in `MigrationRunner` will catch a wrong version number).

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/storage/schema_v7.py src/teams_transcriber/storage/phone.py src/teams_transcriber/storage/__init__.py tests/storage/test_phone_imports.py
git commit -m "feat(storage): phone_imports UID ledger (schema v7)"
```

---

### Task 2: Sync contract — parse/build the JSON files

**Files:**
- Create: `src/teams_transcriber/phone_sync/__init__.py` (empty docstring module)
- Create: `src/teams_transcriber/phone_sync/contract.py`
- Test: `tests/phone_sync/__init__.py` (empty), `tests/phone_sync/test_contract.py`

**Interfaces:**
- Produces (used by Tasks 5-7):
  - `SCHEMA_VERSION: int = 1`
  - `class ContractError(ValueError)`
  - `@dataclass Sidecar: uid: str; title: str; source: str; started_at: str; ended_at: str | None; duration_ms: int | None; app_version: str`
  - `@dataclass TodoChange: recording_id: int; todo_index: int; done: bool; toggled_at: str`
  - `parse_sidecar(text: str) -> Sidecar` — raises `ContractError` on malformed JSON, missing required fields (`uid`, `title`, `source`, `started_at`), or `source` outside `{"teams_call", "in_person", "memo"}`
  - `parse_changes(text: str) -> list[TodoChange]` — malformed entries are skipped (logged), never fatal
  - `build_ack(imported: list[dict], changes_applied_through: str | None) -> str`
  - `build_manifest(desktop_version: str, exported_at: str) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/phone_sync/test_contract.py`:

```python
from __future__ import annotations

import json

import pytest

from teams_transcriber.phone_sync.contract import (
    SCHEMA_VERSION, ContractError, parse_changes, parse_sidecar,
    build_ack, build_manifest,
)


def test_parse_sidecar_roundtrip():
    text = json.dumps({
        "uid": "abc", "title": "Standup", "source": "teams_call",
        "started_at": "2026-07-14T09:00:00+00:00",
        "ended_at": "2026-07-14T09:30:00+00:00",
        "duration_ms": 1_800_000, "app_version": "0.1.0",
    })
    sc = parse_sidecar(text)
    assert (sc.uid, sc.title, sc.source) == ("abc", "Standup", "teams_call")
    assert sc.duration_ms == 1_800_000


def test_parse_sidecar_missing_field_raises():
    with pytest.raises(ContractError):
        parse_sidecar(json.dumps({"uid": "abc", "title": "x", "source": "memo"}))


def test_parse_sidecar_bad_source_raises():
    with pytest.raises(ContractError):
        parse_sidecar(json.dumps({
            "uid": "a", "title": "t", "source": "carrier_pigeon",
            "started_at": "2026-07-14T09:00:00+00:00",
        }))


def test_parse_sidecar_garbage_raises():
    with pytest.raises(ContractError):
        parse_sidecar("not json {")


def test_parse_changes_skips_malformed_entries():
    text = json.dumps([
        {"recording_id": 3, "todo_index": 0, "done": True,
         "toggled_at": "2026-07-14T10:00:00+00:00"},
        {"recording_id": "oops"},
        {"recording_id": 4, "todo_index": 2, "done": False,
         "toggled_at": "2026-07-14T11:00:00+00:00"},
    ])
    changes = parse_changes(text)
    assert [(c.recording_id, c.todo_index, c.done) for c in changes] == [
        (3, 0, True), (4, 2, False),
    ]


def test_parse_changes_garbage_returns_empty():
    assert parse_changes("]{") == []


def test_ack_and_manifest_carry_schema_version():
    ack = json.loads(build_ack(
        [{"uid": "a", "recording_id": 1, "result": "imported"}],
        changes_applied_through="2026-07-14T11:00:00+00:00",
    ))
    assert ack["schema_version"] == SCHEMA_VERSION
    assert ack["imported"][0]["uid"] == "a"
    manifest = json.loads(build_manifest("0.10.1", "2026-07-14T12:00:00+00:00"))
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["desktop_version"] == "0.10.1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/phone_sync/test_contract.py -v`
Expected: FAIL with `ModuleNotFoundError: teams_transcriber.phone_sync`.

- [ ] **Step 3: Implement `contract.py`**

```python
"""The phone↔desktop sync contract: JSON shapes exchanged via the phone's
Documents/TeamsTranscriber/ folder. Single source of truth for schema_version.

Spec: docs/superpowers/specs/2026-07-14-android-companion-design.md.
Parsing is strict for sidecars (a bad sidecar fails that one import) and
tolerant for changes.json (one malformed toggle never blocks the rest).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
VALID_SOURCES = frozenset({"teams_call", "in_person", "memo"})


class ContractError(ValueError):
    """Raised when a phone-written file doesn't match the contract."""


@dataclass(slots=True)
class Sidecar:
    uid: str
    title: str
    source: str
    started_at: str
    ended_at: str | None
    duration_ms: int | None
    app_version: str


@dataclass(slots=True)
class TodoChange:
    recording_id: int
    todo_index: int
    done: bool
    toggled_at: str


def parse_sidecar(text: str) -> Sidecar:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"sidecar is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError("sidecar must be a JSON object")
    missing = [k for k in ("uid", "title", "source", "started_at") if not data.get(k)]
    if missing:
        raise ContractError(f"sidecar missing required fields: {missing}")
    if data["source"] not in VALID_SOURCES:
        raise ContractError(f"sidecar source {data['source']!r} not in {sorted(VALID_SOURCES)}")
    return Sidecar(
        uid=str(data["uid"]),
        title=str(data["title"]),
        source=str(data["source"]),
        started_at=str(data["started_at"]),
        ended_at=(str(data["ended_at"]) if data.get("ended_at") else None),
        duration_ms=(int(data["duration_ms"]) if data.get("duration_ms") is not None else None),
        app_version=str(data.get("app_version", "")),
    )


def parse_changes(text: str) -> list[TodoChange]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("changes.json is not valid JSON, ignoring: %s", exc)
        return []
    if not isinstance(data, list):
        logger.warning("changes.json is not a list, ignoring")
        return []
    out: list[TodoChange] = []
    for entry in data:
        try:
            out.append(TodoChange(
                recording_id=int(entry["recording_id"]),
                todo_index=int(entry["todo_index"]),
                done=bool(entry["done"]),
                toggled_at=str(entry["toggled_at"]),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("skipping malformed change entry %r: %s", entry, exc)
    return out


def build_ack(imported: list[dict], changes_applied_through: str | None) -> str:
    return json.dumps({
        "schema_version": SCHEMA_VERSION,
        "imported": imported,
        "changes_applied_through": changes_applied_through,
    }, indent=2)


def build_manifest(desktop_version: str, exported_at: str) -> str:
    return json.dumps({
        "schema_version": SCHEMA_VERSION,
        "desktop_version": desktop_version,
        "exported_at": exported_at,
    }, indent=2)
```

`phone_sync/__init__.py`: `"""Desktop side of the Android companion sync (spec 2026-07-14)."""`

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/phone_sync -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/phone_sync tests/phone_sync
git commit -m "feat(phone-sync): sync contract parsing and builders"
```

---

### Task 3: Metadata-aware import — importer kwargs + `Pipeline.import_phone_recording`

**Files:**
- Modify: `src/teams_transcriber/audio/importer.py:45-97` (`import_audio_file`)
- Modify: `src/teams_transcriber/pipeline.py` (new method next to `import_audio_file`, ~line 89)
- Test: `tests/audio/test_importer.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: existing `import_audio_file(src, *, db, paths) -> int` and `Pipeline._submit_post_processing(recording_id)`.
- Produces: `import_audio_file(src, *, db, paths, title: str | None = None, started_at_override: datetime | None = None) -> int` (backward compatible — both kwargs optional); `Pipeline.import_phone_recording(src_path: str, *, title: str | None, started_at: datetime | None) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/audio/test_importer.py` (reuse its existing fixture pattern for `db`/`paths` and its tiny valid audio file helper — read the file first and mirror how existing tests build a source file):

```python
def test_import_honors_metadata_overrides(db, tmp_path):
    # build a valid source audio file exactly as this test file's existing
    # tests do (reuse its helper/fixture), then:
    from datetime import UTC, datetime
    from teams_transcriber.audio.importer import import_audio_file
    from teams_transcriber.storage import RecordingRepo

    when = datetime(2026, 7, 14, 9, 0, 0, tzinfo=UTC)
    rid = import_audio_file(
        src, db=db, paths=paths,
        title="Site walkthrough", started_at_override=when,
    )
    rec = RecordingRepo(db).get(rid)
    assert rec.display_title == "Site walkthrough"
    assert rec.started_at == when.isoformat()
```

Append to `tests/test_pipeline.py` (reuse its pipeline fixture with fake transcriber/summarizer):

```python
def test_import_phone_recording_submits_post_processing(...):
    # per this file's existing import test pattern: call
    # pipeline.import_phone_recording(str(src), title="T", started_at=when),
    # drain the executor, assert the fake transcriber saw the new rid and the
    # recording row carries title "T" and started_at == when.isoformat().
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/audio/test_importer.py tests/test_pipeline.py -v -k "metadata or phone_recording"`
Expected: FAIL (`unexpected keyword argument 'title'` / missing method).

- [ ] **Step 3: Implement**

`importer.py` — change the signature and the two derivation sites:

```python
def import_audio_file(
    src: Path,
    *,
    db: Database,
    paths: AppPaths,
    title: str | None = None,
    started_at_override: datetime | None = None,
) -> int:
```

After the probe, replace the `started_at` derivation with:

```python
    if started_at_override is not None:
        started_at = started_at_override
    else:
        try:
            started_at = datetime.fromtimestamp(src.stat().st_mtime, tz=UTC)
        except OSError:
            started_at = datetime.now(UTC)
```

Replace `title = _display_title(src.stem)` with:

```python
    display = title if title else _display_title(src.stem)
    slug_source = title if title else src.stem
```

and use `slug_source` in the `_slug(...)` call for the filename, `display` for both `detected_title` and `display_title`.

`pipeline.py` — next to `import_audio_file`:

```python
    def import_phone_recording(
        self, src_path: str, *, title: str | None, started_at: datetime | None,
    ) -> int:
        """Import a phone-recorded file with sidecar metadata and enqueue
        post-processing. Same flow as import_audio_file, but the title and
        start time come from the phone's sidecar instead of the filename."""
        from pathlib import Path
        from teams_transcriber.audio.importer import import_audio_file
        rid = import_audio_file(
            Path(src_path), db=self._db, paths=self._paths,
            title=title, started_at_override=started_at,
        )
        self._submit_post_processing(rid)
        return rid
```

(Add `from datetime import datetime` to pipeline.py's imports if not present.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/audio/test_importer.py tests/test_pipeline.py tests/test_transcript_importer.py -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/audio/importer.py src/teams_transcriber/pipeline.py tests/audio/test_importer.py tests/test_pipeline.py
git commit -m "feat(pipeline): metadata-aware import for phone recordings"
```

---

### Task 4: Transport interface + `LocalDirTransport`

**Files:**
- Create: `src/teams_transcriber/phone_sync/transport.py`
- Test: `tests/phone_sync/test_transport.py`

**Interfaces:**
- Produces (Task 6 consumes; Phase 2's `MtpTransport` implements the same Protocol):
  - `@dataclass RemoteFile: name: str; size: int` (`name` is a forward-slash relative path, e.g. `"outbox/rec_a.m4a"`)
  - `class Transport(Protocol)` with `list_files(prefix: str) -> list[RemoteFile]`, `pull(name: str, dest: Path) -> None`, `push(src: Path, name: str) -> None`, `push_text(text: str, name: str) -> None`, `read_text(name: str) -> str | None` (None if absent), `delete(name: str) -> None`
  - `class LocalDirTransport(Transport)` over a base directory (creates parent dirs on push; `list_files` recurses under `base/prefix`, returns names sorted)

- [ ] **Step 1: Write the failing tests**

`tests/phone_sync/test_transport.py`:

```python
from __future__ import annotations

from pathlib import Path

from teams_transcriber.phone_sync.transport import LocalDirTransport, RemoteFile


def test_push_list_pull_delete_roundtrip(tmp_path):
    t = LocalDirTransport(tmp_path / "phone")
    src = tmp_path / "a.m4a"
    src.write_bytes(b"audio-bytes")

    t.push(src, "outbox/rec_a.m4a")
    t.push_text('{"x": 1}', "outbox/rec_a.json")

    files = t.list_files("outbox")
    assert [f.name for f in files] == ["outbox/rec_a.json", "outbox/rec_a.m4a"]
    assert RemoteFile("outbox/rec_a.m4a", len(b"audio-bytes")) in files

    dest = tmp_path / "pulled.m4a"
    t.pull("outbox/rec_a.m4a", dest)
    assert dest.read_bytes() == b"audio-bytes"

    assert t.read_text("outbox/rec_a.json") == '{"x": 1}'
    assert t.read_text("outbox/nope.json") is None

    t.delete("outbox/rec_a.m4a")
    assert [f.name for f in t.list_files("outbox")] == ["outbox/rec_a.json"]


def test_list_files_empty_prefix_dir(tmp_path):
    t = LocalDirTransport(tmp_path / "phone")
    assert t.list_files("outbox") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/phone_sync/test_transport.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement `transport.py`**

```python
"""Transport abstraction over the phone's TeamsTranscriber folder.

Names are forward-slash relative paths inside the folder ("outbox/rec_x.m4a").
LocalDirTransport (a plain directory) backs all engine tests and works with
any folder-sync tool; Phase 2 adds MtpTransport for USB.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RemoteFile:
    name: str
    size: int


class Transport(Protocol):
    def list_files(self, prefix: str) -> list[RemoteFile]: ...
    def pull(self, name: str, dest: Path) -> None: ...
    def push(self, src: Path, name: str) -> None: ...
    def push_text(self, text: str, name: str) -> None: ...
    def read_text(self, name: str) -> str | None: ...
    def delete(self, name: str) -> None: ...


class LocalDirTransport:
    def __init__(self, base: Path) -> None:
        self._base = Path(base)

    def _path(self, name: str) -> Path:
        return self._base / Path(name)

    def list_files(self, prefix: str) -> list[RemoteFile]:
        root = self._base / prefix
        if not root.is_dir():
            return []
        out = [
            RemoteFile(
                name=p.relative_to(self._base).as_posix(),
                size=p.stat().st_size,
            )
            for p in root.rglob("*") if p.is_file()
        ]
        return sorted(out, key=lambda f: f.name)

    def pull(self, name: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._path(name), dest)

    def push(self, src: Path, name: str) -> None:
        target = self._path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    def push_text(self, text: str, name: str) -> None:
        target = self._path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def read_text(self, name: str) -> str | None:
        p = self._path(name)
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/phone_sync -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/phone_sync/transport.py tests/phone_sync/test_transport.py
git commit -m "feat(phone-sync): transport interface with LocalDirTransport"
```

---

### Task 5: Library export builder

**Files:**
- Create: `src/teams_transcriber/phone_sync/library_export.py`
- Test: `tests/phone_sync/test_library_export.py`

**Interfaces:**
- Consumes: `RecordingRepo.list_recent(limit)`, `SummaryRepo.get`, `TodoStateRepo.list_for_recording`, `TranscriptRepo.list_for_recording`, `ChatRepo.list_for_recording`, `PhoneImportRepo.source_for_recordings`, `contract.build_manifest`, `teams_transcriber.__version__`.
- Produces (Task 6 consumes): `build_library(db, *, now_iso: str) -> dict[str, str]` — maps relative file names (`library/manifest.json`, `library/meetings.json`, `library/meetings/<id>.json`) to JSON text. `now_iso` injected for deterministic tests.

- [ ] **Step 1: Write the failing test**

`tests/phone_sync/test_library_export.py` — seed a recording + summary + todo state + transcript segment + chat message using the same repo calls as `tests/ui/test_summary_pane.py`'s `db_with_summary` fixture (read that fixture and mirror it against the plain `db` fixture), then:

```python
def test_build_library_full_mirror(db):
    rid = ...  # seeded recording id per the fixture pattern
    from teams_transcriber.phone_sync.library_export import build_library
    from teams_transcriber.storage import PhoneImportRepo

    PhoneImportRepo(db).record("uid-9", rid, "in_person")
    files = build_library(db, now_iso="2026-07-14T12:00:00+00:00")

    manifest = json.loads(files["library/manifest.json"])
    assert manifest["schema_version"] == 1

    meetings = json.loads(files["library/meetings.json"])
    entry = next(m for m in meetings if m["id"] == rid)
    assert entry["title"] and entry["started_at"] and entry["status"] == "done"
    assert entry["source"] == "in_person"          # ledger overrides "manual"
    assert entry["todo_count"] == 2 and entry["todos_done"] == 0

    detail = json.loads(files[f"library/meetings/{rid}.json"])
    assert detail["summary"]
    assert detail["my_todos"][0]["task"] and detail["my_todos"][0]["done"] is False
    assert detail["transcript"][0]["text"]
    assert detail["chat"] == []                    # or seeded messages if added


def test_build_library_skips_recordings_without_summary(db):
    # a recording with no Summary row appears in meetings.json (status visible)
    # but gets no library/meetings/<id>.json detail file
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/phone_sync/test_library_export.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement `library_export.py`**

```python
"""Build the desktop→phone library export (the phone's full mirror).

Pure: db in, {relative name: JSON text} out. The sync engine decides how the
files reach the phone; tests read the dict directly.
"""

from __future__ import annotations

import json

from teams_transcriber import __version__
from teams_transcriber.phone_sync.contract import build_manifest
from teams_transcriber.storage import (
    ChatRepo, Database, PhoneImportRepo, RecordingRepo, SummaryRepo,
    TodoStateRepo, TranscriptRepo,
)

_EXPORT_LIMIT = 1000  # matches the app's personal-scale assumptions


def build_library(db: Database, *, now_iso: str) -> dict[str, str]:
    rec_repo = RecordingRepo(db)
    sum_repo = SummaryRepo(db)
    todo_repo = TodoStateRepo(db)
    tr_repo = TranscriptRepo(db)
    chat_repo = ChatRepo(db)
    phone_sources = PhoneImportRepo(db).source_for_recordings()

    files: dict[str, str] = {
        "library/manifest.json": build_manifest(__version__, now_iso),
    }
    meetings: list[dict] = []

    for rec in rec_repo.list_recent(limit=_EXPORT_LIMIT):
        if rec.id is None:
            continue
        summary = sum_repo.get(rec.id)
        states = {s.todo_index: s for s in todo_repo.list_for_recording(rec.id)}
        todo_count = len(summary.my_todos) if summary else 0
        todos_done = sum(1 for s in states.values() if s.done)
        meetings.append({
            "id": rec.id,
            "title": rec.display_title or rec.detected_title or "Untitled meeting",
            "started_at": rec.started_at,
            "duration_ms": rec.duration_ms,
            "status": rec.status.value,
            "one_line": summary.one_line if summary else None,
            "source": phone_sources.get(rec.id, rec.source.value),
            "todo_count": todo_count,
            "todos_done": todos_done,
        })
        if summary is None:
            continue
        files[f"library/meetings/{rec.id}.json"] = json.dumps({
            "id": rec.id,
            "summary": summary.summary,
            "key_decisions": summary.key_decisions,
            "my_todos": [
                {
                    "index": i,
                    "task": td.task,
                    "due": td.due,
                    "done": bool(states.get(i) and states[i].done),
                    "done_at": states[i].done_at if i in states else None,
                }
                for i, td in enumerate(summary.my_todos)
            ],
            "action_items_others": [
                {"who": a.who, "task": a.task, "due": a.due}
                for a in summary.action_items_others
            ],
            "follow_ups": summary.follow_ups,
            "transcript": [
                {"start_ms": s.start_ms, "channel": s.channel.value, "text": s.text}
                for s in tr_repo.list_for_recording(rec.id)
            ],
            "chat": [
                {"role": m.role, "content": m.content, "created_at": m.created_at}
                for m in chat_repo.list_for_recording(rec.id)
            ],
        }, indent=2)

    files["library/meetings.json"] = json.dumps(meetings, indent=2)
    return files
```

(Adjust attribute access to the real dataclasses while implementing — e.g. `TodoItem.due`, `Channel.value` — the repos and models are in `src/teams_transcriber/storage/`; the export test will catch mismatches.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/phone_sync -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/phone_sync/library_export.py tests/phone_sync/test_library_export.py
git commit -m "feat(phone-sync): full-mirror library export builder"
```

---

### Task 6: Sync engine — pull → import → toggles → export → ack

**Files:**
- Create: `src/teams_transcriber/phone_sync/sync.py`
- Test: `tests/phone_sync/test_sync.py`

**Interfaces:**
- Consumes: `Transport` (Task 4), `contract` (Task 2), `build_library` (Task 5), `PhoneImportRepo` (Task 1), `TodoStateRepo.mark_done(recording_id, todo_index, done, *, task_text=None)` and `.list_for_recording`, an injected `import_recording` callable (Task 3's `Pipeline.import_phone_recording` in production; a fake in tests).
- Produces:
  - `@dataclass PhoneSyncReport: imported: list[tuple[str, int]]; skipped_known: int; toggles_applied: int; toggles_skipped_stale: int; failures: list[tuple[str, str]]`
  - `run_sync(db, transport, *, import_recording: Callable[..., int], on_todos_changed: Callable[[int], None] | None = None, now_iso: str) -> PhoneSyncReport`
    where `import_recording(src_path: str, *, title: str | None, started_at: datetime | None) -> int`.

Engine rules (each is a test):
1. For every `outbox/rec_*.m4a` with a parseable sidecar: skip if uid already in the ledger (`skipped_known`); else pull audio to a temp dir, verify pulled size matches `RemoteFile.size`, call `import_recording`, `PhoneImportRepo.record(uid, rid, source)`, then — only after the ledger row is committed — `transport.delete` both remote files. A pull/size/import failure records a `failures` entry and leaves the remote files untouched.
2. A `rec_*.m4a` without a sidecar, or with a `ContractError` sidecar, is a failure entry; files left in place (the phone owner can inspect).
3. `outbox/changes.json`: parse; for each `TodoChange`, apply last-write-wins — read the current `TodoState` for `(recording_id, todo_index)`; apply `mark_done(..., done=change.done)` only if the change's `toggled_at` is strictly newer than the existing `done_at` (a row with `done_at=None` counts as older than any timestamp; an unknown `recording_id`/index is a failure entry, not a crash). Count applied vs `toggles_skipped_stale`. Call `on_todos_changed(rid)` once per distinct recording that had a toggle applied. The engine never writes `changes.json` (single-writer rule) — `changes_applied_through` in the ack is the max `toggled_at` among applied-or-stale-skipped entries, else None.
4. Always: push every `build_library(db, now_iso=now_iso)` file, then `push_text(build_ack(...), "sync/desktop_ack.json")` with one entry per imported/failed outbox item.

- [ ] **Step 1: Write the failing tests**

`tests/phone_sync/test_sync.py` — all against `LocalDirTransport(tmp_path)` and a fake importer that creates a real Recording row (so the ledger FK and export hold):

```python
# Fixtures: `db` from conftest; a `phone(tmp_path)` helper returning
# LocalDirTransport; `seed_outbox(t, uid, title="T", source="memo")` writing
# rec_<uid>.m4a bytes + a valid sidecar via contract-shaped json.
# fake_import(db) returns a callable that creates a DONE Recording row with
# the given title/started_at and returns its id, recording calls in a list.

def test_happy_path_imports_and_acks(db, tmp_path): ...
    # seed 1 outbox item -> run_sync -> report.imported has (uid, rid);
    # outbox is empty; ledger maps uid; sync/desktop_ack.json lists the uid
    # with result "imported"; library/manifest.json + meetings.json exist.

def test_second_run_is_idempotent(db, tmp_path): ...
    # re-seed the SAME uid after a successful run (simulates a stale copy):
    # second run -> skipped_known == 1, importer called once total,
    # remote duplicate deleted.

def test_missing_sidecar_leaves_files_and_reports_failure(db, tmp_path): ...

def test_pull_size_mismatch_leaves_files(db, tmp_path): ...
    # monkeypatch transport.pull to write truncated bytes -> failure entry,
    # remote files still present, ledger empty.

def test_toggle_lww_phone_newer_wins(db, tmp_path): ...
    # seed recording+summary+todo done_at=10:00; change toggled_at=11:00 ->
    # applied, state flipped, on_todos_changed called with rid once.

def test_toggle_lww_desktop_newer_wins(db, tmp_path): ...
    # done_at=12:00; change toggled_at=11:00 -> toggles_skipped_stale == 1,
    # state unchanged, on_todos_changed NOT called.

def test_ack_changes_applied_through_is_max_seen(db, tmp_path): ...
```

Write them fully (each 10-20 lines) following the seeding patterns from `tests/storage/test_phone_imports.py` (Task 1) and `tests/ui/test_summary_pane.py`'s summary seeding.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/phone_sync/test_sync.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement `sync.py`**

```python
"""One phone-sync cycle: pull outbox → import → apply toggles → export → ack.

Pure orchestration against the Transport protocol — no MTP, no UI, no
threads. Callers wire the pipeline import and the Wrike close-loop callback.
Safety rules (spec): idempotent via the phone_imports ledger; a remote file
is deleted only after its import is committed; last-write-wins on toggles;
the desktop never writes changes.json.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from teams_transcriber.phone_sync import contract
from teams_transcriber.phone_sync.library_export import build_library
from teams_transcriber.phone_sync.transport import Transport
from teams_transcriber.storage import Database, PhoneImportRepo, TodoStateRepo

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PhoneSyncReport:
    imported: list[tuple[str, int]] = field(default_factory=list)
    skipped_known: int = 0
    toggles_applied: int = 0
    toggles_skipped_stale: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def _parse_started_at(sidecar: contract.Sidecar) -> datetime | None:
    try:
        return datetime.fromisoformat(sidecar.started_at)
    except ValueError:
        return None


def run_sync(
    db: Database,
    transport: Transport,
    *,
    import_recording: Callable[..., int],
    on_todos_changed: Callable[[int], None] | None = None,
    now_iso: str,
) -> PhoneSyncReport:
    report = PhoneSyncReport()
    ledger = PhoneImportRepo(db)
    ack_entries: list[dict] = []

    remote = {f.name: f for f in transport.list_files("outbox")}
    audio_names = sorted(n for n in remote if n.endswith(".m4a"))

    for audio_name in audio_names:
        sidecar_name = audio_name[: -len(".m4a")] + ".json"
        stem = Path(audio_name).stem
        if sidecar_name not in remote:
            report.failures.append((audio_name, "missing sidecar"))
            ack_entries.append({"uid": stem, "recording_id": None, "result": "missing sidecar"})
            continue
        sidecar_text = transport.read_text(sidecar_name)
        try:
            sidecar = contract.parse_sidecar(sidecar_text or "")
        except contract.ContractError as exc:
            report.failures.append((audio_name, str(exc)))
            ack_entries.append({"uid": stem, "recording_id": None, "result": f"bad sidecar: {exc}"})
            continue
        if ledger.recording_id_for(sidecar.uid) is not None:
            report.skipped_known += 1
            transport.delete(audio_name)
            transport.delete(sidecar_name)
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="tt-phone-") as tmp:
                local = Path(tmp) / Path(audio_name).name
                transport.pull(audio_name, local)
                if local.stat().st_size != remote[audio_name].size:
                    raise OSError(
                        f"size mismatch: got {local.stat().st_size}, "
                        f"expected {remote[audio_name].size}"
                    )
                rid = import_recording(
                    str(local), title=sidecar.title,
                    started_at=_parse_started_at(sidecar),
                )
            ledger.record(sidecar.uid, rid, sidecar.source)
        except Exception as exc:  # noqa: BLE001 — one bad file never stops the batch
            logger.exception("phone import failed for %s", audio_name)
            report.failures.append((audio_name, str(exc)))
            ack_entries.append({"uid": sidecar.uid, "recording_id": None, "result": f"failed: {exc}"})
            continue
        transport.delete(audio_name)      # only after ledger commit
        transport.delete(sidecar_name)
        report.imported.append((sidecar.uid, rid))
        ack_entries.append({"uid": sidecar.uid, "recording_id": rid, "result": "imported"})

    # --- todo toggles (desktop never writes changes.json) -------------------
    applied_recordings: set[int] = set()
    max_seen: str | None = None
    changes_text = transport.read_text("outbox/changes.json")
    todo_repo = TodoStateRepo(db)
    for change in contract.parse_changes(changes_text or ""):
        max_seen = max(max_seen or change.toggled_at, change.toggled_at)
        states = {
            s.todo_index: s for s in todo_repo.list_for_recording(change.recording_id)
        }
        current = states.get(change.todo_index)
        if current is None:
            report.failures.append((
                "changes.json",
                f"unknown todo {change.recording_id}/{change.todo_index}",
            ))
            continue
        if current.done_at is not None and current.done_at >= change.toggled_at:
            report.toggles_skipped_stale += 1
            continue
        todo_repo.mark_done(change.recording_id, change.todo_index, change.done)
        report.toggles_applied += 1
        applied_recordings.add(change.recording_id)

    if on_todos_changed is not None:
        for rid in sorted(applied_recordings):
            on_todos_changed(rid)

    # --- export + ack --------------------------------------------------------
    for name, text in build_library(db, now_iso=now_iso).items():
        transport.push_text(text, name)
    transport.push_text(
        contract.build_ack(ack_entries, changes_applied_through=max_seen),
        "sync/desktop_ack.json",
    )
    return report
```

Note while implementing: ISO-8601 strings with the same UTC offset compare correctly as strings — both sides write `datetime.isoformat()` UTC; add a one-line comment saying so at the comparison.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/phone_sync -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/phone_sync/sync.py tests/phone_sync/test_sync.py
git commit -m "feat(phone-sync): sync engine — pull, import, LWW toggles, export, ack"
```

---

### Task 7: CLI entry point — `teams-transcriber phone-sync <folder>`

**Files:**
- Modify: `src/teams_transcriber/cli.py` (new subcommand alongside the existing ones)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `run_sync` (Task 6), `Pipeline.import_phone_recording` (Task 3), `LocalDirTransport` (Task 4), `_build_pipeline(paths, with_watcher=False)` (existing).
- Produces: `_cmd_phone_sync(args) -> int` — runs one sync cycle against a folder, prints the report, waits for processing (`pipeline.shutdown()`), returns 0 (1 if any failures). Registered as the `phone-sync` subcommand with one positional `folder` argument.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (mirror the existing subcommand test pattern — read how `retry-summary`/`serve` are tested and dispatched):

```python
def test_phone_sync_command_runs_cycle(tmp_path, monkeypatch, capsys):
    # Arrange a phone folder with one outbox item (valid sidecar + m4a bytes),
    # monkeypatch Pipeline.import_phone_recording to create a DONE Recording
    # row (real repos, no transcription), invoke the CLI main with
    # ["phone-sync", str(phone_dir)], assert exit code 0, outbox emptied,
    # "Imported 1" in captured output, and library/manifest.json exists.
```

Write it fully following that file's conventions (it already has an `AppPaths(root=tmp_path...)` pattern from Task 16 of the UI overhaul).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v -k phone_sync`
Expected: FAIL (unknown command `phone-sync`).

- [ ] **Step 3: Implement**

In `cli.py`, add:

```python
def _cmd_phone_sync(args: argparse.Namespace) -> int:
    """One sync cycle against a plain folder (LocalDirTransport).

    Useful headlessly and with folder-sync tools today; the UI/MTP flow in
    Phase 2 reuses run_sync with a different transport.
    """
    from datetime import UTC, datetime
    from teams_transcriber.phone_sync.sync import run_sync
    from teams_transcriber.phone_sync.transport import LocalDirTransport

    paths = AppPaths()
    paths.ensure_dirs()
    pipeline = _build_pipeline(paths, with_watcher=False)
    try:
        report = run_sync(
            pipeline._db,  # noqa: SLF001 — CLI is app-internal wiring
            LocalDirTransport(Path(args.folder)),
            import_recording=pipeline.import_phone_recording,
            now_iso=datetime.now(UTC).isoformat(),
        )
    finally:
        pipeline.shutdown()   # waits for queued transcribe/summarize work
    print(f"Imported {len(report.imported)}, skipped {report.skipped_known} known, "
          f"toggles applied {report.toggles_applied} "
          f"({report.toggles_skipped_stale} stale), "
          f"failures {len(report.failures)}")
    for name, why in report.failures:
        print(f"  FAILED {name}: {why}")
    return 1 if report.failures else 0
```

Register it where the other subcommands are wired (mirror the existing `add_parser` block; add `from pathlib import Path` import if missing):

```python
    p_phone = sub.add_parser("phone-sync", help="Sync a phone folder (recordings in, library out)")
    p_phone.add_argument("folder", help="Path to the TeamsTranscriber folder (outbox/library/sync)")
    p_phone.set_defaults(func=_cmd_phone_sync)
```

Note: `pipeline._db` — check whether Pipeline exposes the db publicly (e.g. an attribute used by cli already); if `_build_pipeline` can return `(pipeline, db)` more cleanly per existing code, prefer that and adjust — the existing `cli.py` structure decides; don't add new public surface beyond what this command needs.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py tests/phone_sync -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/cli.py tests/test_cli.py
git commit -m "feat(cli): phone-sync command for folder-based sync"
```

---

## Final verification (after Task 7)

- [ ] `uv run pytest` — full suite green (~538 existing + ~25 new).
- [ ] End-to-end dry run: create a folder with a real short `.m4a` + sidecar, run `teams-transcriber phone-sync <folder>` (proxy-scrubbed if summarization should run), confirm the recording appears in the desktop UI and `library/` contains the mirror.
- [ ] `git log --oneline` — one conventional commit per task.
