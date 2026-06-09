# Phase 13 — Multi-Destination Wrike Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-folder Wrike sync with a full planner dialog that lets the user split a meeting's data across multiple Wrike folders, choose per-item format (task vs. comment), and pick assignees from a fuzzy + LLM-suggested list of Wrike contacts.

**Architecture:** A unified `SyncItem` model normalises everything sendable to Wrike. A new `WrikeSyncPlanner` modal renders one row per item with its own destination/format/assignee controls. Auto-sync (on `SummaryReady`) and the manual "Send to Wrike" button both open this planner; the existing `WrikeFolderPicker` lives on only as the inline destination picker. Schema v6 rebuilds `wrike_tasks` to widen its `kind` CHECK constraint and add `format` + `assignee_id` columns.

**Tech Stack:** PySide6 / Qt 6 (UI); httpx (Wrike client, existing); Anthropic SDK with tool-use (assignee LLM fallback, same pattern as `summarizer.py`); SQLite via existing `MigrationRunner`; hand-rolled token-sort-ratio (no rapidfuzz — not in dep tree).

---

## File structure

**Create:**
- `src/teams_transcriber/storage/schema_v6.py` — table-rebuild migration for `wrike_tasks`
- `src/teams_transcriber/integrations/wrike_assignees.py` — fuzzy + LLM resolver
- `src/teams_transcriber/integrations/wrike_items.py` — `SyncItem` model + `recording_to_sync_items`
- `src/teams_transcriber/ui/wrike_sync_planner.py` — the planner dialog
- `tests/storage/test_wrike_schema_v6.py`
- `tests/integrations/test_wrike_assignees.py`
- `tests/integrations/test_wrike_items.py`
- `tests/integrations/test_wrike_sync_items.py`
- `tests/ui/test_wrike_sync_planner.py`

**Modify:**
- `src/teams_transcriber/storage/__init__.py` — export `SCHEMA_V6`, register in migrations list
- `src/teams_transcriber/storage/wrike.py` — `WrikeTaskRow` adds `format`, `assignee_id`; `WrikeTaskRepo.insert` and row-mappers round-trip them
- `src/teams_transcriber/integrations/wrike_client.py` — add `create_comment`
- `src/teams_transcriber/integrations/wrike_sync.py` — add `sync_items(...)` PlanRow-based orchestrator; keep `sync_recording` as a thin shim for any leftover callers (removed in P13-7)
- `src/teams_transcriber/ui/app.py` — `_wrike_open_picker` rewires to the planner; new `_wrike_open_planner`, `_wrike_load_planner_data`, `_wrike_run_plan` methods; toast text + count uses `recording_to_sync_items`
- `tests/integrations/test_wrike_sync.py` — existing tests stay; adapt the toast-flow test in P13-7

---

## Type contract (the shared shapes)

These names are referenced across multiple tasks — keep them consistent.

```python
# wrike_items.py
SyncKind = Literal["summary", "decisions", "my_todo", "action_other", "follow_up"]

@dataclass(slots=True)
class SyncItem:
    kind: SyncKind
    index: int                       # 0 for singletons, per-list index otherwise
    text: str
    suggested_who: str | None        # only set when kind == "action_other"

# wrike_sync.py
SyncFormat = Literal["task", "comment"]

@dataclass(slots=True)
class PlanRow:
    item: SyncItem
    folder_id: str
    format: SyncFormat
    assignee_id: str | None          # only meaningful for action_other tasks

@dataclass(slots=True)
class SyncReport:
    created_tasks: int = 0
    created_comments: int = 0
    skipped_already_synced: int = 0
    failures: list[tuple[SyncItem, str]] = field(default_factory=list)
```

The `wrike_tasks` table stores the comment id in the existing `wrike_task_id` column when `format == "comment"`; the column name stays for back-compat. Document in code where it's written.

---

## Task list

### Task 1: Schema v6 — rebuild `wrike_tasks` with widened CHECK + new columns

**Files:**
- Create: `src/teams_transcriber/storage/schema_v6.py`
- Modify: `src/teams_transcriber/storage/__init__.py`
- Modify: `src/teams_transcriber/storage/wrike.py:20-29` (add `format`, `assignee_id` to `WrikeTaskRow`), `src/teams_transcriber/storage/wrike.py:96-106` (insert), `src/teams_transcriber/storage/wrike.py:108-123` (`get`), `src/teams_transcriber/storage/wrike.py:125-140` (`list_for_recording`)
- Test: `tests/storage/test_wrike_schema_v6.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_wrike_schema_v6.py
"""Verify schema v6 rebuilds wrike_tasks: widens kind CHECK and adds
format/assignee_id columns. Pre-existing rows are preserved with defaults."""

from __future__ import annotations

import sqlite3

import pytest

from teams_transcriber.storage import build_database
from teams_transcriber.storage.db import Database
from teams_transcriber.storage.migrations import MigrationRunner
from teams_transcriber.storage.schema_v1 import SCHEMA_V1
from teams_transcriber.storage.schema_v2 import SCHEMA_V2
from teams_transcriber.storage.schema_v3 import SCHEMA_V3
from teams_transcriber.storage.schema_v4 import SCHEMA_V4
from teams_transcriber.storage.schema_v5 import SCHEMA_V5
from teams_transcriber.storage.schema_v6 import SCHEMA_V6


def _build_v5_db(path) -> Database:
    """Apply schemas v1..v5 only (no v6) to simulate an existing user DB."""
    db = build_database(path)
    with db.connect() as conn:
        MigrationRunner([SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5]).run(conn)
    return db


def test_v6_migration_preserves_existing_rows_and_adds_columns(tmp_path) -> None:
    db = _build_v5_db(tmp_path / "v5.db")
    # Insert a recording + a v5-shape wrike_tasks row.
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO recordings (started_at, source, detected_title, "
            "status, audio_path) VALUES (?, 'manual', 't', 'done', NULL)",
            ("2026-06-09T10:00:00+00:00",),
        )
        rec_id = conn.execute("SELECT id FROM recordings").fetchone()[0]
        conn.execute(
            "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
            "wrike_task_id, wrike_folder_id, created_at, last_synced_done) "
            "VALUES (?, 'my', 0, 'TASK123', 'FOLDER1', ?, 0)",
            (rec_id, "2026-06-09T10:00:00+00:00"),
        )
        conn.commit()

    # Now apply v6.
    with db.connect() as conn:
        MigrationRunner([SCHEMA_V6]).run(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6

        # Existing row preserved with defaults for new columns.
        row = conn.execute(
            "SELECT kind, todo_index, wrike_task_id, format, assignee_id "
            "FROM wrike_tasks WHERE recording_id=?", (rec_id,),
        ).fetchone()
        assert row == ("my", 0, "TASK123", "task", None)

        # Widened CHECK accepts the new kinds.
        for kind in ("summary", "decisions", "follow_up"):
            conn.execute(
                "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
                "wrike_task_id, wrike_folder_id, created_at, last_synced_done, "
                "format, assignee_id) VALUES (?, ?, 0, 'X', 'F', ?, 0, 'comment', NULL)",
                (rec_id, kind, "2026-06-09T10:00:00+00:00"),
            )
        conn.commit()

        # Old narrow values still rejected outside the expanded set.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
                "wrike_task_id, wrike_folder_id, created_at, last_synced_done) "
                "VALUES (?, 'bogus', 9, 'X', 'F', ?, 0)",
                (rec_id, "2026-06-09T10:00:00+00:00"),
            )

        # Format CHECK enforces task/comment only.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
                "wrike_task_id, wrike_folder_id, created_at, last_synced_done, "
                "format, assignee_id) VALUES (?, 'my', 7, 'X', 'F', ?, 0, 'description', NULL)",
                (rec_id, "2026-06-09T10:00:00+00:00"),
            )

        # Index still exists.
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='wrike_tasks_recording_idx'"
        ).fetchone()
        assert idx is not None


def test_v6_cascade_delete_still_works(tmp_path) -> None:
    """Deleting a recording cascades to its wrike_tasks rows after the rebuild."""
    db = _build_v5_db(tmp_path / "cascade.db")
    with db.connect() as conn:
        MigrationRunner([SCHEMA_V6]).run(conn)
        conn.execute(
            "INSERT INTO recordings (started_at, source, detected_title, "
            "status, audio_path) VALUES (?, 'manual', 't', 'done', NULL)",
            ("2026-06-09T10:00:00+00:00",),
        )
        rec_id = conn.execute("SELECT id FROM recordings").fetchone()[0]
        conn.execute(
            "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
            "wrike_task_id, wrike_folder_id, created_at, last_synced_done) "
            "VALUES (?, 'my', 0, 'TASK', 'FOLDER', ?, 0)",
            (rec_id, "2026-06-09T10:00:00+00:00"),
        )
        conn.execute("DELETE FROM recordings WHERE id=?", (rec_id,))
        conn.commit()
        remaining = conn.execute(
            "SELECT COUNT(*) FROM wrike_tasks WHERE recording_id=?", (rec_id,),
        ).fetchone()[0]
        assert remaining == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/storage/test_wrike_schema_v6.py -v`
Expected: ImportError or ModuleNotFoundError on `schema_v6`.

- [ ] **Step 3: Write `schema_v6.py`**

```python
# src/teams_transcriber/storage/schema_v6.py
"""Schema v6: rebuild wrike_tasks with widened kind CHECK + format/assignee_id.

`wrike_tasks.kind` had `CHECK (kind IN ('my', 'other'))` from v4. Phase 13
needs to send summaries, decisions, and follow-ups too, and each row needs
its own format (task | comment) and assignee. SQLite can't ALTER a CHECK
constraint, so we follow the Phase 9 v3 precedent: CREATE new, INSERT SELECT,
DROP old, RENAME. The MigrationRunner toggles `foreign_keys=OFF` around the
migration so the DROP doesn't cascade-delete child rows."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE wrike_tasks_new (
        id                INTEGER PRIMARY KEY,
        recording_id      INTEGER NOT NULL
                          REFERENCES recordings(id) ON DELETE CASCADE,
        kind              TEXT NOT NULL CHECK (kind IN
                              ('my', 'other', 'summary', 'decisions', 'follow_up')),
        todo_index        INTEGER NOT NULL,
        wrike_task_id     TEXT NOT NULL,
        wrike_folder_id   TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        last_synced_done  INTEGER NOT NULL DEFAULT 0,
        format            TEXT NOT NULL DEFAULT 'task'
                          CHECK (format IN ('task', 'comment')),
        assignee_id       TEXT,
        UNIQUE (recording_id, kind, todo_index)
    )
    """,
    """
    INSERT INTO wrike_tasks_new
        (id, recording_id, kind, todo_index, wrike_task_id, wrike_folder_id,
         created_at, last_synced_done, format, assignee_id)
    SELECT id, recording_id, kind, todo_index, wrike_task_id, wrike_folder_id,
           created_at, last_synced_done, 'task', NULL
    FROM wrike_tasks
    """,
    "DROP TABLE wrike_tasks",
    "ALTER TABLE wrike_tasks_new RENAME TO wrike_tasks",
    "CREATE INDEX wrike_tasks_recording_idx ON wrike_tasks (recording_id)",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V6 = Migration(version=6, name="rebuild wrike_tasks for multi-dest", apply=_apply)
```

- [ ] **Step 4: Register the migration in the storage package**

In `src/teams_transcriber/storage/__init__.py`, find where `SCHEMA_V5` is imported + added to the migrations list and add `SCHEMA_V6` immediately after.

```python
# Add to imports:
from teams_transcriber.storage.schema_v6 import SCHEMA_V6
# Update __all__ to include "SCHEMA_V6"
# Add SCHEMA_V6 to the migrations list passed to MigrationRunner.
```

(Re-check the exact pattern in the file. The other SCHEMA_VN entries show the convention. If the migrations list is built explicitly, append `SCHEMA_V6`; if implicit via `__all__` or similar, follow that.)

- [ ] **Step 5: Update `WrikeTaskRow` dataclass**

In `src/teams_transcriber/storage/wrike.py`, replace the `WrikeTaskRow` dataclass with:

```python
@dataclass(slots=True)
class WrikeTaskRow:
    id: int | None
    recording_id: int
    kind: str
    todo_index: int
    wrike_task_id: str        # carries comment-id when format == "comment"
    wrike_folder_id: str
    created_at: str
    last_synced_done: bool
    format: str = "task"      # "task" | "comment"
    assignee_id: str | None = None
```

- [ ] **Step 6: Update `WrikeTaskRepo` to round-trip new columns**

In `src/teams_transcriber/storage/wrike.py`, update `WrikeTaskRepo.insert`, `get`, and `list_for_recording`:

```python
def insert(self, row: WrikeTaskRow) -> int:
    with self._db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
            "wrike_task_id, wrike_folder_id, created_at, last_synced_done, "
            "format, assignee_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row.recording_id, row.kind, row.todo_index, row.wrike_task_id,
             row.wrike_folder_id, row.created_at,
             1 if row.last_synced_done else 0,
             row.format, row.assignee_id),
        )
        conn.commit()
        return cur.lastrowid

def get(self, recording_id: int, kind: str, todo_index: int) -> WrikeTaskRow | None:
    with self._db.connect() as conn:
        cur = conn.execute(
            "SELECT id, recording_id, kind, todo_index, wrike_task_id, "
            "wrike_folder_id, created_at, last_synced_done, format, assignee_id "
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
        format=row[8], assignee_id=row[9],
    )

def list_for_recording(self, recording_id: int) -> list[WrikeTaskRow]:
    with self._db.connect() as conn:
        cur = conn.execute(
            "SELECT id, recording_id, kind, todo_index, wrike_task_id, "
            "wrike_folder_id, created_at, last_synced_done, format, assignee_id "
            "FROM wrike_tasks WHERE recording_id=? ORDER BY kind, todo_index",
            (recording_id,),
        )
        return [
            WrikeTaskRow(
                id=r[0], recording_id=r[1], kind=r[2], todo_index=r[3],
                wrike_task_id=r[4], wrike_folder_id=r[5], created_at=r[6],
                last_synced_done=bool(r[7]),
                format=r[8], assignee_id=r[9],
            )
            for r in cur.fetchall()
        ]
```

- [ ] **Step 7: Run the new test + the existing wrike storage tests**

Run: `uv run pytest tests/storage/test_wrike_schema_v6.py tests/storage/test_wrike.py -v` (or whatever existing wrike storage tests file is named — `grep -r "WrikeTaskRepo\|WrikeSyncRepo" tests/storage/` to find them).
Expected: all pass. Existing tests that constructed `WrikeTaskRow(...)` without `format`/`assignee_id` continue to work via the defaults.

- [ ] **Step 8: Commit**

```bash
git add src/teams_transcriber/storage/schema_v6.py \
        src/teams_transcriber/storage/__init__.py \
        src/teams_transcriber/storage/wrike.py \
        tests/storage/test_wrike_schema_v6.py
git commit -m "feat(storage): schema v6 — rebuild wrike_tasks with format + assignee_id

Widens the kind CHECK from {my, other} to include {summary, decisions,
follow_up}, and adds format ('task'|'comment') and assignee_id columns.
SQLite can't ALTER a CHECK so this is a table rebuild per the Phase 9
precedent. Existing rows preserved with format='task', assignee_id=NULL.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Fuzzy + LLM assignee resolver

**Files:**
- Create: `src/teams_transcriber/integrations/wrike_assignees.py`
- Test: `tests/integrations/test_wrike_assignees.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_wrike_assignees.py
"""Fuzzy + LLM assignee resolver for action_items_others."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from teams_transcriber.integrations.wrike_assignees import (
    Contact,
    suggest_assignees,
    token_sort_ratio,
)


def _contact(cid: str, first: str, last: str) -> Contact:
    return Contact(id=cid, first_name=first, last_name=last)


# --- Fuzzy matcher unit tests ---

def test_token_sort_ratio_handles_order_swap() -> None:
    # "Jennifer Smith" vs "Smith Jennifer" should score identically after sort.
    assert token_sort_ratio("Jennifer Smith", "Smith Jennifer") == pytest.approx(1.0)


def test_token_sort_ratio_partial_first_name() -> None:
    # "Jen" should match "Jennifer Smith" reasonably well (substring tokens).
    assert token_sort_ratio("Jen", "Jennifer Smith") > 0.4


def test_token_sort_ratio_zero_on_disjoint() -> None:
    assert token_sort_ratio("Mike Stone", "Sarah Kim") < 0.2


# --- Resolver behaviour ---

class _FakeBlock:
    def __init__(self, name: str, input_: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = input_


class _FakeResp:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessages:
    def __init__(self, scripted: _FakeResp) -> None:
        self._scripted = scripted
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResp:
        self.calls.append(kwargs)
        return self._scripted


class _FakeClient:
    def __init__(self, scripted: _FakeResp) -> None:
        self.messages = _FakeMessages(scripted)


def test_resolver_returns_exact_full_name_match_without_llm() -> None:
    """When fuzzy is confident, the LLM is never called."""
    contacts = [
        _contact("100", "Jennifer", "Smith"),
        _contact("200", "Mike", "Stone"),
    ]
    items = [
        ("idx-0", "Jennifer Smith"),
        ("idx-1", "Mike Stone"),
    ]
    client = _FakeClient(_FakeResp(content=[]))
    out = suggest_assignees(
        items, contacts,
        meeting_summary="—",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=True,
        anthropic_client_factory=lambda _k: client,
    )
    assert out == {"idx-0": "100", "idx-1": "200"}
    assert client.messages.calls == []


def test_resolver_handles_first_name_only() -> None:
    contacts = [_contact("100", "Jennifer", "Smith")]
    items = [("idx-0", "Jen")]
    client = _FakeClient(_FakeResp(content=[]))
    out = suggest_assignees(
        items, contacts,
        meeting_summary="—",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=False,         # fuzzy only
        anthropic_client_factory=lambda _k: client,
    )
    # "Jen" should fuzz-hit "Jennifer Smith".
    assert out == {"idx-0": "100"}


def test_resolver_returns_none_when_no_confident_match_and_no_llm() -> None:
    contacts = [_contact("100", "Jennifer", "Smith")]
    items = [("idx-0", "the eng lead")]
    client = _FakeClient(_FakeResp(content=[]))
    out = suggest_assignees(
        items, contacts,
        meeting_summary="—",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=False,
        anthropic_client_factory=lambda _k: client,
    )
    assert out == {"idx-0": None}


def test_resolver_falls_back_to_llm_for_unresolved() -> None:
    contacts = [
        _contact("100", "Jennifer", "Smith"),
        _contact("200", "Mike", "Stone"),
    ]
    items = [
        ("idx-0", "Jennifer Smith"),     # fuzzy hit, no LLM needed
        ("idx-1", "the engineering lead"),  # LLM must resolve
        ("idx-2", "someone unknown"),    # LLM returns null
    ]
    fake = _FakeResp(content=[
        _FakeBlock(
            name="resolve_assignees",
            input_={
                "matches": [
                    {"item_index": 1, "contact_id": "200"},
                    {"item_index": 2, "contact_id": None},
                ],
            },
        ),
    ])
    client = _FakeClient(fake)
    out = suggest_assignees(
        items, contacts,
        meeting_summary="Standup with the engineering team",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=True,
        anthropic_client_factory=lambda _k: client,
    )
    assert out == {"idx-0": "100", "idx-1": "200", "idx-2": None}
    # LLM was called exactly once, batched.
    assert len(client.messages.calls) == 1


def test_resolver_swallows_llm_failure_returning_null_for_unresolved() -> None:
    """Network/auth failure in the LLM call should not crash; unresolved → None."""
    contacts = [_contact("100", "Jennifer", "Smith")]
    items = [("idx-0", "the eng lead")]

    class _Boom:
        @property
        def messages(self) -> Any:
            raise RuntimeError("network down")

    out = suggest_assignees(
        items, contacts,
        meeting_summary="—",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=True,
        anthropic_client_factory=lambda _k: _Boom(),
    )
    assert out == {"idx-0": None}


def test_resolver_returns_empty_on_empty_items() -> None:
    out = suggest_assignees(
        [], [_contact("100", "A", "B")],
        meeting_summary="—",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=True,
        anthropic_client_factory=lambda _k: _FakeClient(_FakeResp([])),
    )
    assert out == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integrations/test_wrike_assignees.py -v`
Expected: ImportError on `wrike_assignees`.

- [ ] **Step 3: Implement `wrike_assignees.py`**

```python
# src/teams_transcriber/integrations/wrike_assignees.py
"""Fuzzy + LLM assignee resolver for action_items_others.

Two-pass:
1. Token-sort-ratio against every contact's full name. If best score >= 0.85
   and beats the runner-up by >= 0.10, we take it.
2. (Optional) one batched Claude tool-use call resolves the remaining items
   using meeting summary + the action-item text as context.

The LLM pass is gated on `llm_fallback`. Network/API errors in the LLM call
log a WARNING and treat all unresolved items as None — the planner UI still
opens; the user can pick assignees manually.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Tuning knobs. Conservative thresholds; we'd rather punt to "Unassigned"
# than silently pick the wrong person.
_FUZZY_MIN_SCORE = 0.85
_FUZZY_MIN_MARGIN = 0.10

ItemKey = str  # opaque key the caller uses to identify each item (sync-item index, etc.)
ClientFactory = Callable[[str], Any]


@dataclass(slots=True, frozen=True)
class Contact:
    id: str
    first_name: str
    last_name: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


# --- Token-sort-ratio (hand-rolled; rapidfuzz isn't in the dep tree) ---

def _tokens(s: str) -> list[str]:
    return [t for t in s.lower().split() if t]


def token_sort_ratio(a: str, b: str) -> float:
    """Score in [0.0, 1.0]. Sort the tokens of each side, then character-sequence
    similarity over the sorted strings. Handles order swaps and partial matches.

    Uses `difflib.SequenceMatcher.ratio()` from the stdlib — same algorithm
    rapidfuzz exposes as `token_sort_ratio` (modulo a /100 scaling)."""
    from difflib import SequenceMatcher
    if not a or not b:
        return 0.0
    a_sorted = " ".join(sorted(_tokens(a)))
    b_sorted = " ".join(sorted(_tokens(b)))
    return SequenceMatcher(None, a_sorted, b_sorted).ratio()


def _fuzzy_resolve(name: str, contacts: Sequence[Contact]) -> str | None:
    """Best contact for `name`, or None if not confident enough."""
    if not name.strip() or not contacts:
        return None
    scored = sorted(
        ((token_sort_ratio(name, c.full_name), c) for c in contacts),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best_contact = scored[0]
    runner_up_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < _FUZZY_MIN_SCORE:
        return None
    if best_score - runner_up_score < _FUZZY_MIN_MARGIN:
        return None
    return best_contact.id


# --- LLM fallback ---

_TOOL_NAME = "resolve_assignees"
_TOOL = {
    "name": _TOOL_NAME,
    "description": (
        "For each unresolved action-item, choose the best-matching team-member "
        "id from the provided contacts, or null when no team member is a "
        "confident fit."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_index": {"type": "integer"},
                        "contact_id": {"type": ["string", "null"]},
                    },
                    "required": ["item_index", "contact_id"],
                },
            },
        },
        "required": ["matches"],
    },
}


def _default_client_factory(api_key: str) -> Any:
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def _llm_resolve(
    unresolved: list[tuple[int, str, str]],   # [(idx, who, task_text), ...]
    contacts: Sequence[Contact],
    *,
    meeting_summary: str,
    api_key: str,
    model: str,
    client_factory: ClientFactory,
) -> dict[int, str | None]:
    """Single batched Claude call. Returns {idx: contact_id|None}."""
    if not unresolved:
        return {}
    items_block = "\n".join(
        f"- index={i}  who={who!r}  task={task!r}"
        for i, who, task in unresolved
    )
    contacts_block = "\n".join(
        f"- {c.id}  {c.full_name}" for c in contacts
    )
    user_text = (
        "Meeting summary (for context):\n"
        f"{meeting_summary or '(none provided)'}\n\n"
        "Unresolved action items:\n"
        f"{items_block}\n\n"
        "Team members:\n"
        f"{contacts_block}\n\n"
        "For each unresolved item, call resolve_assignees with the matches."
    )
    try:
        client = client_factory(api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user_text}],
        )
    except Exception:
        logger.warning("assignee LLM resolver failed; treating all unresolved as None",
                       exc_info=True)
        return {idx: None for idx, _, _ in unresolved}

    # Find the tool_use block.
    payload: dict[str, Any] | None = None
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == _TOOL_NAME:
            raw = getattr(block, "input", None)
            if isinstance(raw, dict):
                payload = raw
                break
            if isinstance(raw, str):
                try:
                    payload = json.loads(raw)
                    break
                except json.JSONDecodeError:
                    pass

    out: dict[int, str | None] = {idx: None for idx, _, _ in unresolved}
    if not payload:
        return out
    valid_ids = {c.id for c in contacts}
    for m in payload.get("matches", []) or []:
        try:
            idx = int(m["item_index"])
            cid_raw = m.get("contact_id")
        except (KeyError, TypeError, ValueError):
            continue
        if cid_raw is None:
            out[idx] = None
        elif isinstance(cid_raw, str) and cid_raw in valid_ids:
            out[idx] = cid_raw
        # else: ignore garbage ids (model hallucinated)
    return out


# --- Public entry point ---

def suggest_assignees(
    items: Sequence[tuple[ItemKey, str]],     # [(item_key, raw_who), ...]
    contacts: Sequence[Contact],
    *,
    meeting_summary: str | None,
    api_key: str | None,
    model: str,
    llm_fallback: bool,
    anthropic_client_factory: ClientFactory | None = None,
) -> dict[ItemKey, str | None]:
    """Return {item_key: contact_id or None} for each input item."""
    if not items:
        return {}
    factory = anthropic_client_factory or _default_client_factory

    # Stable enumeration so the LLM's "item_index" maps back to ItemKey deterministically.
    keys: list[ItemKey] = [k for k, _ in items]
    whos: list[str] = [w or "" for _, w in items]

    resolved: dict[ItemKey, str | None] = {}
    unresolved_for_llm: list[tuple[int, str, str]] = []

    for i, (key, who) in enumerate(items):
        if not who or not who.strip():
            resolved[key] = None
            continue
        hit = _fuzzy_resolve(who, contacts)
        if hit is not None:
            resolved[key] = hit
        else:
            resolved[key] = None
            # Surface to LLM if enabled + we have an api key.
            unresolved_for_llm.append((i, who, who))  # task_text == who here; caller can extend

    if llm_fallback and api_key and unresolved_for_llm:
        llm_out = _llm_resolve(
            unresolved_for_llm, contacts,
            meeting_summary=meeting_summary or "",
            api_key=api_key, model=model,
            client_factory=factory,
        )
        for i, hit in llm_out.items():
            if 0 <= i < len(keys):
                resolved[keys[i]] = hit

    return resolved
```

- [ ] **Step 4: Run tests until green**

Run: `uv run pytest tests/integrations/test_wrike_assignees.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/integrations/wrike_assignees.py \
        tests/integrations/test_wrike_assignees.py
git commit -m "feat(wrike): fuzzy + LLM assignee resolver

Two-pass resolver: hand-rolled token_sort_ratio (no rapidfuzz dep)
catches confident matches; remaining items go through one batched Claude
tool-use call. LLM failure falls back gracefully — unresolved items are
None and the user picks manually in the planner.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `SyncItem` model + `recording_to_sync_items`

**Files:**
- Create: `src/teams_transcriber/integrations/wrike_items.py`
- Test: `tests/integrations/test_wrike_items.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_wrike_items.py
"""Convert a stored Recording + Summary into a list of SyncItem."""

from __future__ import annotations

from teams_transcriber.integrations.wrike_items import (
    SyncItem,
    recording_to_sync_items,
)
from teams_transcriber.storage import (
    ActionItemOther,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    Summary,
    SummaryRepo,
    TodoItem,
    build_database,
)


def _seed(tmp_path):
    db = build_database(tmp_path / "items.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00",
        ended_at="2026-06-09T11:00:00+00:00",
        source=RecordingSource.MANUAL,
        detected_title="t", display_title="Q3 sync",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="Q3 sync",
        one_line="x",
        summary="We aligned on Q3 priorities.",
        key_decisions=["Ship in July", "Hire 2 PMs"],
        my_todos=[
            TodoItem(task="Email Jennifer"),
            TodoItem(task="Order banner"),
        ],
        action_items_others=[
            ActionItemOther(who="Sarah Kim", task="Migration doc"),
            ActionItemOther(who="the eng lead", task="IAM cutover"),
        ],
        follow_ups=["Revisit pricing", "Schedule next sync"],
        topics=[],
        generated_at="2026-06-09T10:00:00+00:00",
        model_used="claude-sonnet-4-6",
    ))
    return db, rec.id


def test_items_order_is_stable_and_complete(tmp_path) -> None:
    db, rid = _seed(tmp_path)
    items = recording_to_sync_items(db, rid)

    # Expected: 1 summary, 1 decisions, 2 my_todo, 2 action_other, 2 follow_up.
    kinds = [i.kind for i in items]
    assert kinds == [
        "summary", "decisions",
        "my_todo", "my_todo",
        "action_other", "action_other",
        "follow_up", "follow_up",
    ]
    assert items[0].text == "We aligned on Q3 priorities."
    # Decisions text is a bulleted block.
    assert "Ship in July" in items[1].text and "Hire 2 PMs" in items[1].text
    # My-todos preserve order.
    assert [i.text for i in items[2:4]] == ["Email Jennifer", "Order banner"]
    # Action-others carry suggested_who.
    assert items[4].suggested_who == "Sarah Kim"
    assert items[5].suggested_who == "the eng lead"
    # Follow-ups are individual items.
    assert items[6].text == "Revisit pricing"
    assert items[7].text == "Schedule next sync"

    # Indices: singletons 0, lists per-element.
    assert items[0].index == 0
    assert items[1].index == 0
    assert items[2].index == 0 and items[3].index == 1
    assert items[4].index == 0 and items[5].index == 1
    assert items[6].index == 0 and items[7].index == 1

    db.close()


def test_items_skips_missing_sections(tmp_path) -> None:
    """A meeting with only my_todos should produce only my_todo items."""
    db = build_database(tmp_path / "min.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="min",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="min", one_line=None, summary=None,
        my_todos=[TodoItem(task="Just one")],
        action_items_others=[], key_decisions=[], follow_ups=[], topics=[],
        generated_at="2026-06-09T10:00:00+00:00", model_used="m",
    ))
    items = recording_to_sync_items(db, rec.id)
    assert [i.kind for i in items] == ["my_todo"]
    db.close()


def test_returns_empty_for_unknown_recording(tmp_path) -> None:
    db = build_database(tmp_path / "empty.db")
    db.initialize()
    assert recording_to_sync_items(db, 9999) == []
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integrations/test_wrike_items.py -v`
Expected: ImportError on `wrike_items`.

- [ ] **Step 3: Implement `wrike_items.py`**

```python
# src/teams_transcriber/integrations/wrike_items.py
"""SyncItem model + conversion from a Recording/Summary into a stable list.

`recording_to_sync_items(db, rid)` is the single conversion point so the
planner, the orchestrator, and any tests all see the same item ordering.
Ordering matters because the planner uses positional indices and the
WrikeTaskRepo uses (kind, todo_index) for idempotency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from teams_transcriber.storage import SummaryRepo
from teams_transcriber.storage.db import Database

SyncKind = Literal["summary", "decisions", "my_todo", "action_other", "follow_up"]


@dataclass(slots=True)
class SyncItem:
    kind: SyncKind
    index: int                       # 0 for singletons, per-list-element otherwise
    text: str
    suggested_who: str | None = None  # action_other only


def _decisions_block(decisions: list[str]) -> str:
    return "\n".join(f"- {d}" for d in decisions)


def recording_to_sync_items(db: Database, recording_id: int) -> list[SyncItem]:
    """Stable ordering: summary, decisions, my_todos (in order),
    action_items_others (in order), follow_ups (in order)."""
    summary = SummaryRepo(db).get(recording_id)
    if summary is None:
        return []
    items: list[SyncItem] = []
    if summary.summary:
        items.append(SyncItem(kind="summary", index=0, text=summary.summary))
    if summary.key_decisions:
        items.append(SyncItem(
            kind="decisions", index=0, text=_decisions_block(summary.key_decisions),
        ))
    for i, td in enumerate(summary.my_todos):
        # Preserve the "(due X)" annotation in the title for the user's review.
        title = td.task + (f" (due {td.due})" if td.due else "")
        items.append(SyncItem(kind="my_todo", index=i, text=title))
    for j, ai in enumerate(summary.action_items_others):
        title = ai.task + (f" (due {ai.due})" if ai.due else "")
        items.append(SyncItem(
            kind="action_other", index=j, text=title, suggested_who=ai.who,
        ))
    for k, f in enumerate(summary.follow_ups):
        items.append(SyncItem(kind="follow_up", index=k, text=f))
    return items
```

- [ ] **Step 4: Run tests until green**

Run: `uv run pytest tests/integrations/test_wrike_items.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/integrations/wrike_items.py \
        tests/integrations/test_wrike_items.py
git commit -m "feat(wrike): SyncItem model + recording_to_sync_items

Single conversion point from a Recording+Summary into the unified
SyncItem list the planner and orchestrator share. Stable ordering so
(kind, index) is a deterministic idempotency key.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: WrikeClient `create_comment`

**Files:**
- Modify: `src/teams_transcriber/integrations/wrike_client.py:66-73` (insert below `create_task`)
- Test: `tests/integrations/test_wrike_client_comments.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_wrike_client_comments.py
"""WrikeClient.create_comment posts to /folders/{id}/comments or /tasks/{id}/comments."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from teams_transcriber.integrations.wrike_client import WrikeClient


def _transport(handler):
    return httpx.MockTransport(handler)


def test_create_comment_on_folder() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"data": [{"id": "C123"}]})

    client = WrikeClient(token="t", transport=_transport(handler))
    cid = client.create_comment(entity_type="folder", entity_id="F1", text="hello")
    assert cid == "C123"
    assert seen["url"].endswith("/folders/F1/comments")
    assert seen["body"] == {"text": "hello"}
    client.close()


def test_create_comment_on_task() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [{"id": "C9"}]})

    client = WrikeClient(token="t", transport=_transport(handler))
    cid = client.create_comment(entity_type="task", entity_id="T7", text="ok")
    assert cid == "C9"
    assert seen["url"].endswith("/tasks/T7/comments")
    client.close()


def test_create_comment_rejects_bad_entity_type() -> None:
    client = WrikeClient(token="t", transport=_transport(lambda r: httpx.Response(200, json={"data": []})))
    with pytest.raises(ValueError):
        client.create_comment(entity_type="project", entity_id="P1", text="x")  # type: ignore[arg-type]
    client.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integrations/test_wrike_client_comments.py -v`
Expected: AttributeError — `create_comment` doesn't exist.

- [ ] **Step 3: Add `create_comment` to WrikeClient**

In `src/teams_transcriber/integrations/wrike_client.py`, add this method below `create_task` (before `complete_task`):

```python
def create_comment(
    self,
    *,
    entity_type: str,         # "folder" | "task"
    entity_id: str,
    text: str,
) -> str:
    """POST /folders/{id}/comments or /tasks/{id}/comments. Returns the comment id."""
    if entity_type not in ("folder", "task"):
        raise ValueError(
            f"entity_type must be 'folder' or 'task', got {entity_type!r}"
        )
    path = f"/{entity_type}s/{entity_id}/comments"
    data = self._request("POST", path, json={"text": text})
    return str(data[0]["id"]) if data else ""
```

- [ ] **Step 4: Run tests until green**

Run: `uv run pytest tests/integrations/test_wrike_client_comments.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/integrations/wrike_client.py \
        tests/integrations/test_wrike_client_comments.py
git commit -m "feat(wrike): WrikeClient.create_comment for folders + tasks

POST /folders/{id}/comments and /tasks/{id}/comments. Returns the new
comment id. Used by the multi-destination planner to ship summaries and
follow-ups as comments rather than tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: PlanRow + `sync_items` orchestrator

**Files:**
- Modify: `src/teams_transcriber/integrations/wrike_sync.py` (add `PlanRow`, `SyncReport`, `sync_items`)
- Test: `tests/integrations/test_wrike_sync_items.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_wrike_sync_items.py
"""sync_items: idempotent multi-destination sync orchestrator."""

from __future__ import annotations

from typing import Any

from teams_transcriber.integrations.wrike_items import SyncItem
from teams_transcriber.integrations.wrike_sync import PlanRow, SyncReport, sync_items
from teams_transcriber.storage import (
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    build_database,
)
from teams_transcriber.storage.wrike import WrikeTaskRepo


class _FakeClient:
    def __init__(self) -> None:
        self.tasks: list[tuple[str, dict[str, Any]]] = []
        self.comments: list[tuple[str, str, str]] = []
        self._next_task_id = 100
        self._next_comment_id = 1000

    def create_task(self, folder_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.tasks.append((folder_id, payload))
        tid = str(self._next_task_id); self._next_task_id += 1
        return {"id": tid}

    def create_comment(self, *, entity_type: str, entity_id: str, text: str) -> str:
        self.comments.append((entity_type, entity_id, text))
        cid = f"C{self._next_comment_id}"; self._next_comment_id += 1
        return cid


def _seed_recording(tmp_path):
    db = build_database(tmp_path / "sync.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="Q3 sync",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    return db, rec.id


def test_sync_items_creates_tasks_and_comments(tmp_path) -> None:
    db, rid = _seed_recording(tmp_path)
    plan = [
        PlanRow(
            item=SyncItem(kind="summary", index=0, text="we aligned"),
            folder_id="F_PROJ", format="comment", assignee_id=None,
        ),
        PlanRow(
            item=SyncItem(kind="my_todo", index=0, text="Email J"),
            folder_id="F_TODOS", format="task", assignee_id=None,
        ),
        PlanRow(
            item=SyncItem(kind="action_other", index=0,
                          text="Migration doc", suggested_who="Sarah"),
            folder_id="F_PROJ", format="task", assignee_id="200",
        ),
    ]
    client = _FakeClient()
    report = sync_items(db, rid, plan, client=client)
    assert report.created_tasks == 2
    assert report.created_comments == 1
    assert report.skipped_already_synced == 0
    assert report.failures == []

    # Tasks landed in their per-row folders.
    folders = sorted(f for f, _ in client.tasks)
    assert folders == ["F_PROJ", "F_TODOS"]
    # Comment landed on the summary's folder.
    assert client.comments == [("folder", "F_PROJ", "we aligned")]
    # Persistence: WrikeTaskRepo has three rows with correct format/assignee.
    rows = sorted(
        WrikeTaskRepo(db).list_for_recording(rid),
        key=lambda r: (r.kind, r.todo_index),
    )
    assert [(r.kind, r.format, r.assignee_id) for r in rows] == [
        ("action_other", "task", "200"),
        ("my_todo", "task", None),
        ("summary", "comment", None),
    ]
    db.close()


def test_sync_items_is_idempotent(tmp_path) -> None:
    db, rid = _seed_recording(tmp_path)
    plan = [
        PlanRow(
            item=SyncItem(kind="my_todo", index=0, text="t"),
            folder_id="F", format="task", assignee_id=None,
        ),
    ]
    client = _FakeClient()
    sync_items(db, rid, plan, client=client)
    # Re-running with the same plan must NOT create a duplicate task.
    report2 = sync_items(db, rid, plan, client=client)
    assert report2.created_tasks == 0
    assert report2.skipped_already_synced == 1
    assert len(client.tasks) == 1
    db.close()


def test_sync_items_partial_failure_continues(tmp_path) -> None:
    db, rid = _seed_recording(tmp_path)

    class _PartFail:
        def __init__(self) -> None:
            self.tasks = 0
            self.comments = 0

        def create_task(self, folder_id, payload):
            self.tasks += 1
            if "BOOM" in payload["title"]:
                raise RuntimeError("api 500")
            return {"id": "T" + str(self.tasks)}

        def create_comment(self, *, entity_type, entity_id, text):
            self.comments += 1
            return "C" + str(self.comments)

    plan = [
        PlanRow(item=SyncItem(kind="my_todo", index=0, text="ok-1"),
                folder_id="F", format="task", assignee_id=None),
        PlanRow(item=SyncItem(kind="my_todo", index=1, text="BOOM"),
                folder_id="F", format="task", assignee_id=None),
        PlanRow(item=SyncItem(kind="my_todo", index=2, text="ok-2"),
                folder_id="F", format="task", assignee_id=None),
    ]
    client = _PartFail()
    report = sync_items(db, rid, plan, client=client)
    assert report.created_tasks == 2
    assert len(report.failures) == 1
    assert "BOOM" in report.failures[0][0].text
    db.close()


def test_sync_items_carries_assignee_for_action_other(tmp_path) -> None:
    db, rid = _seed_recording(tmp_path)
    plan = [
        PlanRow(
            item=SyncItem(kind="action_other", index=0,
                          text="task body", suggested_who="Sarah"),
            folder_id="F", format="task", assignee_id="200",
        ),
    ]
    client = _FakeClient()
    sync_items(db, rid, plan, client=client)
    folder_id, payload = client.tasks[0]
    assert payload["responsibles"] == ["200"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integrations/test_wrike_sync_items.py -v`
Expected: ImportError on `PlanRow` / `SyncReport` / `sync_items`.

- [ ] **Step 3: Add the types + orchestrator to `wrike_sync.py`**

Append to `src/teams_transcriber/integrations/wrike_sync.py` (keep existing `sync_recording` for now — it's the v1 shim and gets replaced in P13-7):

```python
from dataclasses import field

from teams_transcriber.integrations.wrike_items import SyncItem
from teams_transcriber.storage.wrike import WrikeTaskRow


@dataclass(slots=True)
class PlanRow:
    item: SyncItem
    folder_id: str
    format: str       # "task" | "comment"
    assignee_id: str | None


@dataclass(slots=True)
class SyncReport:
    created_tasks: int = 0
    created_comments: int = 0
    skipped_already_synced: int = 0
    failures: list[tuple[SyncItem, str]] = field(default_factory=list)


def _sync_kind_to_db_kind(k: str) -> str:
    """Database column accepts the SyncKind values directly post-v6."""
    return k


def sync_items(
    db: Database,
    recording_id: int,
    plan: list[PlanRow],
    *,
    client: _ClientProto,
) -> SyncReport:
    """Run the planner's PlanRow list. Idempotent on (recording_id, kind, index).

    On per-row failure we accumulate the error and continue with the rest;
    callers surface partial successes via the report.
    """
    rec = RecordingRepo(db).get(recording_id)
    rec_title = (rec.display_title if rec else None) or "Meeting"
    started_at = (rec.started_at if rec else "")[:10]

    task_repo = WrikeTaskRepo(db)
    already = {(r.kind, r.todo_index) for r in task_repo.list_for_recording(recording_id)}
    report = SyncReport()

    for row in plan:
        item = row.item
        db_kind = _sync_kind_to_db_kind(item.kind)
        if (db_kind, item.index) in already:
            report.skipped_already_synced += 1
            continue
        try:
            if row.format == "task":
                base_desc = _build_description(rec_title, started_at, None)
                payload: dict[str, Any] = {
                    "title": item.text if len(item.text) <= 100 else item.text[:97] + "…",
                    "description": item.text if item.text != base_desc else base_desc,
                    "status": "Active",
                }
                if row.assignee_id:
                    payload["responsibles"] = [row.assignee_id]
                created = client.create_task(row.folder_id, payload)
                ref_id = str(created["id"])
                report.created_tasks += 1
            elif row.format == "comment":
                ref_id = client.create_comment(
                    entity_type="folder",
                    entity_id=row.folder_id,
                    text=item.text,
                )
                report.created_comments += 1
            else:
                raise ValueError(f"unknown format: {row.format!r}")

            task_repo.insert(WrikeTaskRow(
                id=None, recording_id=recording_id,
                kind=db_kind, todo_index=item.index,
                wrike_task_id=ref_id, wrike_folder_id=row.folder_id,
                created_at=_now_iso(), last_synced_done=False,
                format=row.format, assignee_id=row.assignee_id,
            ))
        except Exception as exc:
            logger.warning("sync_items: %s/%d failed: %s", item.kind, item.index, exc)
            report.failures.append((item, str(exc)))

    return report
```

Also extend the `_ClientProto` Protocol near the top of the file to include `create_comment`:

```python
class _ClientProto(Protocol):
    def test_connection(self) -> dict[str, Any]: ...
    def list_contacts(self) -> list[dict[str, Any]]: ...
    def create_task(self, folder_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def complete_task(self, task_id: str, *, done: bool) -> dict[str, Any]: ...
    def create_comment(self, *, entity_type: str, entity_id: str, text: str) -> str: ...
```

- [ ] **Step 4: Run tests until green**

Run: `uv run pytest tests/integrations/test_wrike_sync_items.py tests/integrations/test_wrike_sync.py -v`
Expected: new tests PASS; existing `sync_recording` tests still PASS (the shim is unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/integrations/wrike_sync.py \
        tests/integrations/test_wrike_sync_items.py
git commit -m "feat(wrike): sync_items orchestrator + PlanRow

PlanRow-based, idempotent on (rid, kind, index), routes tasks vs.
comments per-row, persists format + assignee_id back to wrike_tasks.
Per-row failures accumulate into SyncReport instead of aborting.
sync_recording stays as the v0.7 single-folder shim until app wiring
moves over.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `WrikeSyncPlanner` widget

**Files:**
- Create: `src/teams_transcriber/ui/wrike_sync_planner.py`
- Test: `tests/ui/test_wrike_sync_planner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_wrike_sync_planner.py
"""WrikeSyncPlanner: rows, gated format dropdowns, lock-when-synced, footer counts."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QPushButton

from teams_transcriber.integrations.wrike_items import SyncItem
from teams_transcriber.integrations.wrike_assignees import Contact
from teams_transcriber.ui.wrike_sync_planner import WrikeSyncPlanner


def _items() -> list[SyncItem]:
    return [
        SyncItem(kind="summary", index=0, text="we aligned"),
        SyncItem(kind="decisions", index=0, text="- Ship in July"),
        SyncItem(kind="my_todo", index=0, text="Email Jennifer"),
        SyncItem(kind="action_other", index=0, text="Migration doc",
                 suggested_who="Sarah Kim"),
        SyncItem(kind="follow_up", index=0, text="Revisit pricing"),
    ]


def _folders() -> list[dict]:
    return [{"id": "F1", "title": "Project A"}, {"id": "F2", "title": "Project B"}]


def _contacts() -> list[Contact]:
    return [
        Contact(id="100", first_name="Jennifer", last_name="Smith"),
        Contact(id="200", first_name="Sarah", last_name="Kim"),
    ]


def test_planner_renders_one_row_per_item(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=_items(),
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={3: "200"},
        already_synced_keys=set(),
    )
    # All 5 row checkboxes are present + checked by default.
    row_cbs = dlg.findChildren(QCheckBox)
    enabled_cbs = [cb for cb in row_cbs if cb.objectName() == "row-include"]
    assert len(enabled_cbs) == 5
    assert all(cb.isChecked() for cb in enabled_cbs)


def test_planner_format_dropdown_is_kind_gated(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=_items(),
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={},
        already_synced_keys=set(),
    )
    combos = [c for c in dlg.findChildren(QComboBox) if c.objectName() == "row-format"]
    # 5 items → 5 format combos.
    assert len(combos) == 5
    # Summary / decisions / follow_up dropdowns are multi-option; my_todo + action_other are task-only.
    options_by_kind = {item.kind: [combos[i].itemText(j) for j in range(combos[i].count())]
                       for i, item in enumerate(_items())}
    assert set(options_by_kind["summary"]) == {"Comment", "Task"}
    assert set(options_by_kind["decisions"]) == {"Comment", "Task"}
    assert options_by_kind["my_todo"] == ["Task"]
    assert options_by_kind["action_other"] == ["Task"]
    assert set(options_by_kind["follow_up"]) == {"Task", "Comment"}


def test_planner_locks_synced_rows(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=_items(),
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={},
        already_synced_keys={("my_todo", 0)},
    )
    cbs = [cb for cb in dlg.findChildren(QCheckBox) if cb.objectName() == "row-include"]
    # The my_todo row (index 2 in the items list) is disabled + still checked (visual "synced").
    locked_cb = cbs[2]
    assert locked_cb.isChecked()
    assert not locked_cb.isEnabled()
    # Footer counts EXCLUDE locked rows.
    send_btn = next(b for b in dlg.findChildren(QPushButton) if b.objectName() == "send-btn")
    assert "Send 4" in send_btn.text()


def test_planner_footer_count_updates_on_uncheck(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=_items(),
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={},
        already_synced_keys=set(),
    )
    send_btn = next(b for b in dlg.findChildren(QPushButton) if b.objectName() == "send-btn")
    assert "Send 5" in send_btn.text()
    cbs = [cb for cb in dlg.findChildren(QCheckBox) if cb.objectName() == "row-include"]
    cbs[0].setChecked(False)
    assert "Send 4" in send_btn.text()


def test_planner_build_plan_returns_only_checked_unlocked(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=_items(),
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={3: "200"},
        already_synced_keys={("decisions", 0)},  # row 1 locked
    )
    # Uncheck the action_other row (3) so the plan should contain 3 rows.
    cbs = [cb for cb in dlg.findChildren(QCheckBox) if cb.objectName() == "row-include"]
    cbs[3].setChecked(False)
    plan = dlg.build_plan()
    kinds = [r.item.kind for r in plan]
    # summary + my_todo + follow_up = 3 rows. decisions excluded (locked), action_other excluded (unchecked).
    assert kinds == ["summary", "my_todo", "follow_up"]
    # Defaults: summary → comment, my_todo → task, follow_up → task.
    assert [r.format for r in plan] == ["comment", "task", "task"]
    # Destination: defaults to LRU head F1.
    assert all(r.folder_id == "F1" for r in plan)


def test_planner_send_disabled_when_no_default_folder(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=[_items()[0]],
        folders=[],                  # no folders → no default
        recent_folder_ids=[],
        contacts=[],
        assignee_suggestions={},
        already_synced_keys=set(),
    )
    send_btn = next(b for b in dlg.findChildren(QPushButton) if b.objectName() == "send-btn")
    assert not send_btn.isEnabled()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_wrike_sync_planner.py -v`
Expected: ImportError on `wrike_sync_planner`.

- [ ] **Step 3: Implement `wrike_sync_planner.py`**

```python
# src/teams_transcriber/ui/wrike_sync_planner.py
"""Themed frameless modal: pick destination + format + assignee per SyncItem.

The dialog replaces WrikeFolderPicker as the entry point for both auto-sync
and manual "Send to Wrike". WrikeFolderPicker still exists and is opened
inline from each row's destination button (DRY)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from teams_transcriber.integrations.wrike_assignees import Contact
from teams_transcriber.integrations.wrike_items import SyncItem
from teams_transcriber.integrations.wrike_sync import PlanRow
from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.title_bar import TitleBar


# Per-kind UI defaults.
_FORMAT_OPTIONS: dict[str, list[str]] = {
    "summary":      ["Comment", "Task"],
    "decisions":    ["Comment", "Task"],
    "my_todo":      ["Task"],
    "action_other": ["Task"],
    "follow_up":    ["Task", "Comment"],
}
_DEFAULT_FORMAT: dict[str, str] = {
    "summary": "Comment", "decisions": "Comment",
    "my_todo": "Task", "action_other": "Task", "follow_up": "Task",
}
_KIND_LABEL: dict[str, str] = {
    "summary": "Summary", "decisions": "Decisions",
    "my_todo": "My todo", "action_other": "Action item", "follow_up": "Follow-up",
}


def _preview(text: str, max_chars: int = 80) -> str:
    one = " ".join(text.split())
    return one if len(one) <= max_chars else one[: max_chars - 1] + "…"


def _label_to_format(label: str) -> str:
    return "task" if label == "Task" else "comment"


class WrikeSyncPlanner(FramelessWindowMixin, QDialog):
    def __init__(
        self,
        *,
        items: list[SyncItem],
        folders: list[dict[str, Any]],
        recent_folder_ids: list[str],
        contacts: list[Contact],
        assignee_suggestions: dict[int, str | None],   # items-list-index → contact_id
        already_synced_keys: Iterable[tuple[str, int]],  # set of (kind, index)
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Send to Wrike")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(720, 520)

        self._items = items
        self._folders = folders
        self._folder_by_id = {f["id"]: f for f in folders}
        self._contacts = contacts
        self._already_synced = set(already_synced_keys)
        # Per-row state. Each entry is a dict with widgets + chosen ids.
        self._rows: list[dict[str, Any]] = []
        # Default folder = LRU head if available, else first folder, else None.
        self._default_folder_id: str | None = (
            recent_folder_ids[0] if recent_folder_ids
            else (folders[0]["id"] if folders else None)
        )

        frame = QFrame(); frame.setObjectName("OuterFrame")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0); shell.addWidget(frame)
        inner = QVBoxLayout(frame); inner.setContentsMargins(0, 0, 0, 0); inner.setSpacing(0)

        self._title_bar = TitleBar(title="Send to Wrike", controls=("close",))
        self._title_bar.close_requested.connect(self.reject)
        inner.addWidget(self._title_bar)

        body = QWidget()
        v = QVBoxLayout(body); v.setContentsMargins(16, 12, 16, 12); v.setSpacing(10)

        # Scroll container for the rows.
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        rows_host = QWidget()
        rows_layout = QVBoxLayout(rows_host); rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(8)
        scroll.setWidget(rows_host)
        v.addWidget(scroll, 1)

        # Build a row per item.
        for i, item in enumerate(items):
            suggested = assignee_suggestions.get(i)
            row_widget, row_state = self._build_row(i, item, suggested)
            rows_layout.addWidget(row_widget)
            self._rows.append(row_state)

        rows_layout.addStretch(1)

        # Footer.
        footer = QHBoxLayout(); footer.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.setProperty("role", "secondary")
        cancel.clicked.connect(self.reject); footer.addWidget(cancel)
        self._send_btn = QPushButton("")
        self._send_btn.setObjectName("send-btn")
        self._send_btn.setProperty("role", "primary"); self._send_btn.setDefault(True)
        self._send_btn.clicked.connect(self._on_accept)
        footer.addWidget(self._send_btn)
        v.addLayout(footer)

        inner.addWidget(body, 1)
        self._init_frameless(frame, resizable=True, title_bar=self._title_bar)
        self._refresh_footer()

    def _build_row(
        self,
        item_idx: int,
        item: SyncItem,
        suggested_assignee_id: str | None,
    ) -> tuple[QWidget, dict[str, Any]]:
        row = QFrame(); row.setProperty("card", True)
        rl = QVBoxLayout(row); rl.setContentsMargins(12, 8, 12, 8); rl.setSpacing(6)

        top = QHBoxLayout(); top.setSpacing(8)

        cb = QCheckBox(); cb.setObjectName("row-include")
        cb.setChecked(True)
        top.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)

        kind_chip = QLabel(_KIND_LABEL[item.kind])
        kind_chip.setProperty("role", "chip")
        kind_chip.setMaximumWidth(120)
        top.addWidget(kind_chip)

        preview = QLabel(_preview(item.text))
        preview.setWordWrap(True); preview.setMinimumWidth(0)
        preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
        top.addWidget(preview, 1)

        synced_badge = QLabel("✓ synced"); synced_badge.setProperty("role", "chip")
        top.addWidget(synced_badge)

        format_cb = QComboBox(); format_cb.setObjectName("row-format")
        for opt in _FORMAT_OPTIONS[item.kind]:
            format_cb.addItem(opt)
        format_cb.setCurrentText(_DEFAULT_FORMAT[item.kind])
        top.addWidget(format_cb)

        # Destination button. Click opens WrikeFolderPicker; label updated on selection.
        dest_btn = QPushButton(); dest_btn.setObjectName("row-dest")
        dest_btn.setProperty("role", "secondary")
        dest_btn.setText(self._folder_label(self._default_folder_id))
        dest_btn.clicked.connect(lambda _=False, b=dest_btn: self._pick_folder(b))
        top.addWidget(dest_btn)

        rl.addLayout(top)

        # Assignee combo for action_other only — on its own line.
        assignee_cb: QComboBox | None = None
        if item.kind == "action_other":
            assignee_row = QHBoxLayout(); assignee_row.setContentsMargins(28, 0, 0, 0)
            assignee_label = QLabel("Assignee:"); assignee_label.setProperty("role", "muted")
            assignee_row.addWidget(assignee_label)
            assignee_cb = QComboBox(); assignee_cb.setObjectName("row-assignee")
            assignee_cb.setEditable(True)
            # First entry: Unassigned. Then all contacts.
            assignee_cb.addItem("Unassigned", userData=None)
            for c in self._contacts:
                assignee_cb.addItem(c.full_name, userData=c.id)
            if suggested_assignee_id is not None:
                # Select the suggested contact (find by userData).
                for j in range(assignee_cb.count()):
                    if assignee_cb.itemData(j) == suggested_assignee_id:
                        assignee_cb.setCurrentIndex(j)
                        break
            assignee_row.addWidget(assignee_cb, 1)
            rl.addLayout(assignee_row)

        state: dict[str, Any] = {
            "item": item,
            "item_idx": item_idx,
            "checkbox": cb,
            "format_combo": format_cb,
            "dest_button": dest_btn,
            "dest_folder_id": self._default_folder_id,
            "assignee_combo": assignee_cb,
            "synced_badge": synced_badge,
            "locked": (item.kind, item.index) in self._already_synced,
        }

        if state["locked"]:
            cb.setEnabled(False)
            format_cb.setEnabled(False)
            dest_btn.setEnabled(False)
            if assignee_cb is not None:
                assignee_cb.setEnabled(False)
        else:
            synced_badge.setVisible(False)

        cb.toggled.connect(self._refresh_footer)
        format_cb.currentTextChanged.connect(self._refresh_footer)
        return row, state

    def _folder_label(self, folder_id: str | None) -> str:
        if folder_id is None:
            return "Pick a folder…"
        f = self._folder_by_id.get(folder_id)
        return f["title"] if f else "Pick a folder…"

    def _pick_folder(self, dest_btn: QPushButton) -> None:
        from teams_transcriber.ui.wrike_folder_picker import WrikeFolderPicker
        recent = [self._default_folder_id] if self._default_folder_id else []
        dlg = WrikeFolderPicker(
            folders=self._folders, recent_folder_ids=recent, parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted or not dlg.selected_folder_id:
            return
        fid = dlg.selected_folder_id
        dest_btn.setText(self._folder_label(fid))
        # Update the row's state by matching button identity.
        for state in self._rows:
            if state["dest_button"] is dest_btn:
                state["dest_folder_id"] = fid
                break
        self._refresh_footer()

    def _refresh_footer(self) -> None:
        count = sum(
            1 for s in self._rows
            if not s["locked"] and s["checkbox"].isChecked()
        )
        self._send_btn.setText(f"Send {count} →")
        # Disable Send when no checked rows OR any checked row has no folder.
        can_send = count > 0 and all(
            s["dest_folder_id"] is not None
            for s in self._rows
            if not s["locked"] and s["checkbox"].isChecked()
        )
        self._send_btn.setEnabled(can_send)

    def _on_accept(self) -> None:
        self.accept()

    def build_plan(self) -> list[PlanRow]:
        out: list[PlanRow] = []
        for s in self._rows:
            if s["locked"] or not s["checkbox"].isChecked():
                continue
            assert s["dest_folder_id"] is not None  # gated by Send
            assignee = None
            if s["assignee_combo"] is not None:
                idx = s["assignee_combo"].currentIndex()
                assignee = s["assignee_combo"].itemData(idx)
            out.append(PlanRow(
                item=s["item"],
                folder_id=s["dest_folder_id"],
                format=_label_to_format(s["format_combo"].currentText()),
                assignee_id=assignee,
            ))
        return out
```

- [ ] **Step 4: Run tests until green**

Run: `uv run pytest tests/ui/test_wrike_sync_planner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/wrike_sync_planner.py \
        tests/ui/test_wrike_sync_planner.py
git commit -m "feat(wrike): WrikeSyncPlanner — per-item destination + format + assignee

Themed frameless modal that replaces WrikeFolderPicker as the entry point
for Wrike sync. One row per SyncItem with kind-gated format dropdown,
destination button that pops the existing folder picker, and an inline
assignee combo for action_others. Locked-when-synced rows show a badge.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: App integration — Settings + planner wiring

**Files:**
- Modify: `src/teams_transcriber/ui/app.py` (`_wrike_open_picker`, new methods, toast text)
- Modify: `src/teams_transcriber/ui/settings_dialog.py` (add LLM-assignee checkbox to the Integrations tab — locate by `wrike_enabled` checkbox)
- Test: `tests/ui/test_wrike_planner_flow.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_wrike_planner_flow.py
"""End-to-end wiring: toast and Send-to-Wrike button open the new planner."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from teams_transcriber.integrations.wrike_items import SyncItem


def test_wrike_open_picker_loads_items_and_opens_planner(qapp, tmp_path, monkeypatch) -> None:
    """Ensure `_wrike_open_picker(rid)` produces a WrikeSyncPlanner pre-populated
    with the recording's SyncItems, the cached folder list, and assignee
    suggestions."""
    # This is an integration test — pull the App together with a stub
    # WrikeClient and stub keyring. Mark as expected to need rework once
    # the implementer touches app.py; the schema of the test is the contract.
    pytest.skip("wire after P13-7 lands; the test scaffolding is provided in the plan")
```

(The Step-1 test is a stub because end-to-end App-level tests in this codebase
are sparse. After Step 3 lands, the implementer SHOULD un-skip this and
finish the wiring assertion below. For TDD purposes, the meaningful failing
unit test for Step 1 lives next to the helper extracted in Step 3.)

```python
# Add to tests/ui/test_wrike_planner_flow.py (this is the real Step-1 failing test):
from teams_transcriber.ui.app import _wrike_open_planner_kwargs   # NEW pure helper


def test_open_planner_kwargs_builds_correct_dialog_inputs(tmp_path) -> None:
    """`_wrike_open_planner_kwargs(db, rid, folders, contacts, suggestions, synced)`
    is a pure function so the App can keep its threading concerns out of the
    UI builder. We test the helper directly."""
    # Will be implemented in Step 3 below.
    from teams_transcriber.storage import (
        Recording, RecordingRepo, RecordingSource, RecordingStatus,
        Summary, SummaryRepo, TodoItem, build_database,
    )
    from teams_transcriber.ui.app import _wrike_open_planner_kwargs

    db = build_database(tmp_path / "f.db"); db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="m",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="m", one_line=None, summary="body",
        my_todos=[TodoItem(task="a")], action_items_others=[],
        key_decisions=[], follow_ups=[], topics=[],
        generated_at="2026-06-09T10:00:00+00:00", model_used="m",
    ))
    kwargs = _wrike_open_planner_kwargs(
        db, rec.id,
        folders=[{"id": "F1", "title": "Proj"}],
        recent_folder_ids=["F1"],
        contacts=[],
        assignee_suggestions={},
    )
    assert [i.kind for i in kwargs["items"]] == ["summary", "my_todo"]
    assert kwargs["recent_folder_ids"] == ["F1"]
    assert kwargs["already_synced_keys"] == set()
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_wrike_planner_flow.py -v`
Expected: ImportError on `_wrike_open_planner_kwargs`.

- [ ] **Step 3: Extract the pure helper in `app.py`**

Add a module-level helper near the other `_wrike_*` helpers in `src/teams_transcriber/ui/app.py`:

```python
def _wrike_open_planner_kwargs(
    db,
    recording_id: int,
    *,
    folders: list,
    recent_folder_ids: list[str],
    contacts: list,
    assignee_suggestions: dict[int, str | None],
) -> dict:
    """Build the keyword arguments for WrikeSyncPlanner. Pure — used to keep
    the threaded App method testable without a QApplication."""
    from teams_transcriber.integrations.wrike_items import recording_to_sync_items
    from teams_transcriber.storage.wrike import WrikeTaskRepo

    items = recording_to_sync_items(db, recording_id)
    already_rows = WrikeTaskRepo(db).list_for_recording(recording_id)
    already = {(r.kind, r.todo_index) for r in already_rows}
    return {
        "items": items,
        "folders": folders,
        "recent_folder_ids": recent_folder_ids,
        "contacts": contacts,
        "assignee_suggestions": assignee_suggestions,
        "already_synced_keys": already,
    }
```

- [ ] **Step 4: Run unit test until green**

Run: `uv run pytest tests/ui/test_wrike_planner_flow.py -v`
Expected: PASS for `test_open_planner_kwargs_builds_correct_dialog_inputs`.

- [ ] **Step 5: Replace `_wrike_open_picker` with a planner-launching flow**

In `src/teams_transcriber/ui/app.py`, REPLACE the body of `_wrike_open_picker` (lines ~961-1001) to fetch BOTH folders AND contacts in the worker, run assignee suggestions, and then open the planner on the main thread:

```python
def _wrike_open_picker(self, recording_id: int) -> None:
    """Now an alias for the planner flow — kept so existing signal connections
    still work. New code should call `_wrike_open_planner` directly."""
    self._wrike_open_planner(recording_id)


def _wrike_open_planner(self, recording_id: int) -> None:
    """Fetch folders + contacts in a worker, resolve assignees, then show the planner."""
    import keyring
    import threading
    from PySide6.QtCore import QTimer

    from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE
    from teams_transcriber.integrations.wrike_assignees import Contact, suggest_assignees
    from teams_transcriber.integrations.wrike_client import (
        WrikeClient, WrikeApiError,
    )
    from teams_transcriber.integrations.wrike_items import recording_to_sync_items
    from teams_transcriber.storage import SummaryRepo

    token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
    if not token:
        show_in_app_toast(
            "Wrike not configured",
            "Add a token in Settings → Integrations.",
        )
        return

    items = recording_to_sync_items(self.db, recording_id)
    if not items:
        show_in_app_toast("Nothing to send", "This recording has no syncable items.")
        return

    summary = SummaryRepo(self.db).get(recording_id)
    meeting_summary_text = (summary.summary if summary else "") or ""
    anthropic_key = self._anthropic_key()  # existing Phase 12 helper
    llm_enabled = bool(
        self.settings._raw.get("integrations", {}).get(
            "wrike_llm_assignee_fallback", True,
        )
    )
    model = self.settings.ai_model

    def _worker() -> None:
        client = WrikeClient(token=token)
        try:
            folders = client.list_folders()
            # Only fetch contacts if there's at least one action_other.
            need_contacts = any(it.kind == "action_other" for it in items)
            contacts_raw = client.list_contacts() if need_contacts else []
        except WrikeApiError as exc:
            QTimer.singleShot(0, self.window, lambda e=str(exc):
                              self._wrike_picker_load_failed(recording_id, e))
            return
        except Exception as exc:
            logger.exception("Wrike planner preload failed")
            QTimer.singleShot(0, self.window, lambda e=str(exc):
                              self._wrike_picker_load_failed(recording_id, e))
            return
        finally:
            client.close()

        contacts = [
            Contact(
                id=str(c.get("id")),
                first_name=str(c.get("firstName") or "").strip(),
                last_name=str(c.get("lastName") or "").strip(),
            )
            for c in contacts_raw
        ]
        action_other_items = [
            (i, it.suggested_who or "")
            for i, it in enumerate(items)
            if it.kind == "action_other"
        ]
        suggestions = suggest_assignees(
            action_other_items, contacts,
            meeting_summary=meeting_summary_text,
            api_key=anthropic_key, model=model,
            llm_fallback=llm_enabled and bool(anthropic_key),
        ) if action_other_items else {}

        QTimer.singleShot(0, self.window, lambda:
            self._wrike_planner_show(
                recording_id, folders, contacts, suggestions, token,
            ),
        )

    threading.Thread(target=_worker, daemon=True).start()


def _wrike_planner_show(
    self,
    recording_id: int,
    folders: list,
    contacts: list,
    assignee_suggestions: dict,
    token: str,
) -> None:
    from teams_transcriber.config import save_settings
    from teams_transcriber.ui.wrike_sync_planner import WrikeSyncPlanner

    recent_ids = list(
        self.settings._raw.get("integrations", {})
        .get("wrike_recent_folder_ids", []) or []
    )
    kwargs = _wrike_open_planner_kwargs(
        self.db, recording_id,
        folders=folders, recent_folder_ids=recent_ids,
        contacts=contacts, assignee_suggestions=assignee_suggestions,
    )
    dlg = WrikeSyncPlanner(parent=self.window, **kwargs)
    if dlg.exec() != dlg.DialogCode.Accepted:
        return
    plan = dlg.build_plan()
    if not plan:
        return
    # Push the most-frequent folder in the plan to the LRU head.
    primary_folder = max(
        (r.folder_id for r in plan),
        key=lambda fid: sum(1 for r in plan if r.folder_id == fid),
    )
    new_recent = _wrike_lru_push(recent_ids, primary_folder, cap=5)
    self.settings._raw.setdefault("integrations", {})[
        "wrike_recent_folder_ids"
    ] = new_recent
    save_settings(self.paths, self.settings)
    import threading
    threading.Thread(
        target=self._wrike_run_plan,
        args=(recording_id, plan, primary_folder, token),
        daemon=True,
    ).start()


def _wrike_run_plan(
    self, recording_id: int, plan: list, primary_folder: str, token: str,
) -> None:
    """Background-thread sync. Updates wrike_sync + toasts the result."""
    from PySide6.QtCore import QTimer

    from teams_transcriber.integrations.wrike_client import (
        WrikeApiError, WrikeClient,
    )
    from teams_transcriber.integrations.wrike_sync import sync_items
    from teams_transcriber.storage.wrike import WrikeSyncRepo

    client = WrikeClient(token=token)
    try:
        report = sync_items(self.db, recording_id, plan, client=client)
        WrikeSyncRepo(self.db).update(
            recording_id,
            status="synced" if not report.failures else "failed",
            folder_id=primary_folder,
            error_message=(
                None if not report.failures
                else f"{len(report.failures)} item(s) failed"
            ),
        )
        title = "Synced to Wrike"
        bits: list[str] = []
        if report.created_tasks:
            bits.append(f"{report.created_tasks} task{'s' if report.created_tasks != 1 else ''}")
        if report.created_comments:
            bits.append(f"{report.created_comments} comment{'s' if report.created_comments != 1 else ''}")
        if report.skipped_already_synced:
            bits.append(f"{report.skipped_already_synced} already synced")
        body = ", ".join(bits) or "Nothing to do."
        if report.failures:
            title = "Wrike sync — partial failure"
            body = f"{body} · {len(report.failures)} failed"
        QTimer.singleShot(0, self.window, lambda: show_in_app_toast(title, body))
    except WrikeApiError as exc:
        WrikeSyncRepo(self.db).update(
            recording_id, status="failed", error_message=str(exc),
        )
        err = str(exc)
        QTimer.singleShot(0, self.window, lambda: show_in_app_toast("Wrike sync failed", err))
    finally:
        client.close()
```

DELETE the old `_wrike_picker_show` and `_wrike_run_sync` methods (no longer reachable).

- [ ] **Step 6: Update the auto-sync toast text + count**

In `_on_summary_ready_wrike` (around lines 929-959 of `app.py`), replace the body around the existing `n = len(s.my_todos) + len(s.action_items_others)` line so the count reflects all sync items:

```python
def _on_summary_ready_wrike(self, evt) -> None:
    """Offer to open the Wrike planner for this summary via a toast."""
    import keyring
    from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE
    from teams_transcriber.integrations.wrike_items import recording_to_sync_items
    from teams_transcriber.storage.wrike import WrikeSyncRepo

    token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
    enabled = bool(
        self.settings._raw.get("integrations", {}).get("wrike_enabled", False)
    )
    existing = WrikeSyncRepo(self.db).get(evt.recording_id)
    already_synced = bool(existing and existing.status == "synced")
    if not _wrike_should_offer_sync(
        enabled=enabled, has_token=bool(token), already_synced=already_synced,
    ):
        return
    items = recording_to_sync_items(self.db, evt.recording_id)
    if not items:
        return
    n = len(items)
    WrikeSyncRepo(self.db).upsert(evt.recording_id, status="pending")
    rid = evt.recording_id
    show_in_app_toast(
        "Send to Wrike",
        f"{n} item{'s' if n != 1 else ''} ready — review and send.",
        action_label="Review",
        action_callback=lambda: self._wrike_open_planner(rid),
    )
```

- [ ] **Step 7: Settings tab — LLM-assignee toggle**

In `src/teams_transcriber/ui/settings_dialog.py`, find the Integrations tab where the `wrike_enabled` checkbox lives (search `wrike_enabled`). Add a new checkbox just below it:

```python
# Inside the Integrations tab builder, near the wrike_enabled checkbox:
self._wrike_llm_assignees_cb = QCheckBox(
    "Use Claude to suggest assignees for ambiguous names"
)
self._wrike_llm_assignees_cb.setToolTip(
    "When sending to Wrike, ask Claude to resolve action-items where the "
    "name doesn't exactly match a Wrike contact (e.g. \"the eng lead\"). "
    "One extra API call per sync."
)
self._wrike_llm_assignees_cb.setChecked(bool(
    self._settings_raw.get("integrations", {}).get(
        "wrike_llm_assignee_fallback", True,
    )
))
# Add to the layout below the existing wrike_enabled checkbox.
integrations_layout.addWidget(self._wrike_llm_assignees_cb)
```

And in the `_on_accept` (or settings-save) path, persist it:

```python
self._settings_raw.setdefault("integrations", {})[
    "wrike_llm_assignee_fallback"
] = self._wrike_llm_assignees_cb.isChecked()
```

(Exact layout names will differ — read the file and follow the convention used for `wrike_enabled`.)

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q`
Expected: ALL pass. The number bumps by approximately the new tests added in Tasks 1-6 + the helper test in this task.

Also run:
`uv run python -c "import teams_transcriber.ui.app; print('OK')"`

Expected: prints `OK`.

- [ ] **Step 9: Commit**

```bash
git add src/teams_transcriber/ui/app.py \
        src/teams_transcriber/ui/settings_dialog.py \
        tests/ui/test_wrike_planner_flow.py
git commit -m "feat(wrike): wire planner flow + LLM-assignee setting

Replace the single-folder picker entry point with WrikeSyncPlanner.
_wrike_open_picker is now an alias for the planner flow so existing signal
connections keep working. SummaryReady toast and SummaryPane.wrike_sync_requested
both route through _wrike_open_planner, which fetches folders + contacts in a
worker thread, runs suggest_assignees, and shows the planner on the main
thread (3-arg QTimer.singleShot for thread safety).

Settings → Integrations gains a wrike_llm_assignee_fallback checkbox
(default ON). When off, only the fuzzy resolver runs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:** Goals 1–4 → planner UI (Task 6) + sync_items (Task 5) + assignee resolver (Task 2) + locked-when-synced (Tasks 5 + 6).
- **Schema rebuild:** Task 1 covers it correctly. Cascade-delete test included.
- **Threading:** Task 7 uses `QTimer.singleShot(0, self.window, …)` (3-arg form) per the Phase 11 lesson.
- **Test count:** roughly +25 tests across the 7 tasks (5 schema + 7 assignees + 3 items + 3 client + 4 sync_items + 6 planner + 1 helper).
- **No new deps.** `rapidfuzz` consciously NOT added; hand-rolled token-sort-ratio over stdlib's `difflib.SequenceMatcher`.
- **`sync_recording` lifecycle:** kept as a shim through Task 5; Task 7 stops calling it. Removal is plan-time — leave for a follow-up commit if any test still references it.

---

## Pre-commit gates (run before each task's commit)

- `uv run pytest <changed test files> -v`
- For Tasks 1, 5, 7: `uv run pytest tests/integrations tests/storage tests/ui -q` to catch downstream breakage.
- For Task 7 specifically: `uv run python -c "import teams_transcriber.ui.app; print('OK')"`.
