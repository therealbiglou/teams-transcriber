# Wrike Project Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-create a Wrike **project** per meeting on `SummaryReady` — summary + key decisions as the description, transcript attached as Markdown, to-dos/follow-ups/action-items-for-others as tasks, notes as a comment — replacing the item-by-item "Send to Wrike" planner.

**Architecture:** Extend the existing `integrations/wrike_*` layer: add project/attachment/space methods to `WrikeClient`, a pure `wrike_project_export.export_recording` orchestrator, pure Markdown body builders, a `wrike_projects` mapping table (schema v9) for idempotent re-push, and a Settings destination picker. Reuse `WrikeClient`, assignee matching (`wrike_assignees.suggest_assignees`), `create_task`, `create_comment`, and the `wrike_sync` pending/retry queue. Retire the planner, folder picker, and `sync_items`/`SyncItem` flow.

**Tech Stack:** Python 3.11, httpx (Wrike REST v4), SQLite via existing storage layer, PySide6 (Settings + picker only), pytest.

## Global Constraints

- uv-only tooling (`uv run pytest`; fallback `.venv\Scripts\python.exe -m pytest`). Bash tool for git. Conventional commits; every commit message ends with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_018PYRS2ZXeA6Z4ra2xkRsDz`
- Spec: `docs/superpowers/specs/2026-07-24-wrike-project-export-design.md` — its decisions are binding: auto on SummaryReady; **create-once, hands-off** (NO done-state close-loop, no reverse sync); re-push updates in place (description + replace transcript attachment + add only new tasks), never duplicates; description = summary + `## Key decisions`; transcript = `.md` attachment; tasks = my-todos + follow-ups + action-items-for-others (assigned); notes = a comment; chat history NOT synced.
- Wrike API shapes: Space list `GET /spaces`; project = `POST /folders/{parentId}/folders` with a `project` field; description update `PUT /folders/{id}`; attachment `POST /folders/{id}/attachments` (raw body + `X-File-Name` header); attachment delete `DELETE /attachments/{id}`; a project/folder object carries a `permalink` for "Open in Wrike". Existing `create_task(folder_id, payload)` and `create_comment(entity_type, entity_id, text)` work against a project id (a project IS a folder).
- All Wrike I/O runs on a worker thread; UI/toasts hop via the 3-arg `QTimer.singleShot(0, self.window, callable)` pattern. No `QMessageBox`; `show_in_app_toast` only. Settings persist via `settings._raw["integrations"]` + `save_settings`.
- Run the full suite before each commit; keep it green (test count shifts as planner tests are removed in Task 8).

## File structure

- Modify `integrations/wrike_client.py` — new project/attachment/space methods (Task 1).
- Create `storage/schema_v9.py` + extend `storage/wrike.py` — `wrike_projects` table + `WrikeProjectRepo` (Task 2).
- Create `integrations/wrike_project_body.py` — pure Markdown builders (Task 3).
- Create `integrations/wrike_project_export.py` — the orchestrator (Task 4).
- Create `ui/wrike_destination_picker.py` + modify `ui/settings_dialog.py` — destination picker + Integrations tab (Task 5, 6).
- Modify `ui/app.py` — SummaryReady + manual push → orchestrator worker; drop close-loop wiring (Task 7).
- Delete `ui/wrike_sync_planner.py`, `ui/wrike_folder_picker.py`, `integrations/wrike_sync.py`, `integrations/wrike_items.py` + tests; remove dead app.py helpers (Task 8).

---

### Task 1: `WrikeClient` — spaces, project create/update, attachments

**Files:**
- Modify: `src/teams_transcriber/integrations/wrike_client.py`
- Test: `tests/integrations/test_wrike_client.py`

**Interfaces:**
- Consumes: existing `WrikeClient._request`, `WrikeApiError`.
- Produces:
  - `list_spaces() -> list[dict]` — `GET /spaces`.
  - `create_project(parent_id: str, title: str, description: str) -> dict` — `POST /folders/{parent_id}/folders`, body `{"title": title, "description": description, "project": {}}`; returns the folder object (has `id`, `permalink`).
  - `update_project(project_id: str, *, description: str) -> dict` — `PUT /folders/{project_id}`, body `{"description": description}`.
  - `upload_attachment(entity_id: str, filename: str, content: bytes) -> str` — `POST /folders/{entity_id}/attachments` with `content` as the raw request body and header `X-File-Name: filename`; returns the new attachment id.
  - `delete_attachment(attachment_id: str) -> None` — `DELETE /attachments/{attachment_id}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integrations/test_wrike_client.py` (mirror its existing `MockTransport`/`_client(handler)` fixtures — read the file first; it already builds a `WrikeClient` with an httpx `MockTransport` that inspects `request` and returns canned JSON):

```python
def test_list_spaces():
    def handler(request):
        assert request.url.path == "/api/v4/spaces"
        return httpx.Response(200, json={"data": [{"id": "sp1", "title": "Team"}]})
    client = _client(handler)
    assert client.list_spaces() == [{"id": "sp1", "title": "Team"}]


def test_create_project_sets_project_field():
    seen = {}
    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"id": "prj1", "permalink": "https://wrike/open.htm?id=1"}]})
    client = _client(handler)
    out = client.create_project("parent1", "Q3 sync", "the description")
    assert seen["path"] == "/api/v4/folders/parent1/folders"
    assert seen["body"]["title"] == "Q3 sync"
    assert seen["body"]["description"] == "the description"
    assert "project" in seen["body"]           # the field that makes it a project
    assert out["id"] == "prj1" and out["permalink"].endswith("id=1")


def test_update_project_description():
    seen = {}
    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"id": "prj1"}]})
    client = _client(handler)
    client.update_project("prj1", description="new desc")
    assert seen["method"] == "PUT" and seen["path"] == "/api/v4/folders/prj1"
    assert seen["body"] == {"description": "new desc"}


def test_upload_attachment_sends_raw_body_and_filename_header():
    seen = {}
    def handler(request):
        seen["path"] = request.url.path
        seen["name"] = request.headers.get("X-File-Name")
        seen["content"] = request.content
        return httpx.Response(200, json={"data": [{"id": "att9"}]})
    client = _client(handler)
    att_id = client.upload_attachment("prj1", "transcript.md", b"# hi\nbody")
    assert seen["path"] == "/api/v4/folders/prj1/attachments"
    assert seen["name"] == "transcript.md"
    assert seen["content"] == b"# hi\nbody"
    assert att_id == "att9"


def test_delete_attachment():
    seen = {}
    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"data": []})
    client = _client(handler)
    client.delete_attachment("att9")
    assert seen["method"] == "DELETE" and seen["path"] == "/api/v4/attachments/att9"
```

Add `import json` / `import httpx` at the top of the test file if not present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integrations/test_wrike_client.py -v -k "spaces or project or attachment"`
Expected: FAIL (`AttributeError: 'WrikeClient' object has no attribute 'list_spaces'`).

- [ ] **Step 3: Implement**

`_request` currently JSON-encodes and returns `data` as a list. `upload_attachment` needs a raw-body request that isn't JSON, and `delete_attachment` returns an empty list — so add the four methods, using `_request` for the JSON ones and a small raw path for the attachment upload:

```python
    def list_spaces(self) -> list[dict[str, Any]]:
        return self._request("GET", "/spaces")

    def create_project(self, parent_id: str, title: str, description: str) -> dict[str, Any]:
        data = self._request(
            "POST", f"/folders/{parent_id}/folders",
            json={"title": title, "description": description, "project": {}},
        )
        if not data:
            raise WrikeApiError(f"Wrike returned no folder for create_project under {parent_id}")
        return data[0]

    def update_project(self, project_id: str, *, description: str) -> dict[str, Any]:
        data = self._request("PUT", f"/folders/{project_id}", json={"description": description})
        return data[0] if data else {}

    def upload_attachment(self, entity_id: str, filename: str, content: bytes) -> str:
        # Wrike's attach endpoint takes the raw file bytes as the request body
        # (not multipart) with the name in the X-File-Name header. Bypass
        # _request's JSON path; reuse its error handling shape.
        resp = self._client.request(
            "POST", f"/folders/{entity_id}/attachments",
            content=content,
            headers={"X-File-Name": filename, "content-type": "application/octet-stream"},
        )
        if resp.status_code in (401, 403):
            raise WrikeAuthError(f"Wrike auth failed ({resp.status_code}): {resp.text[:200]}")
        if not resp.is_success:
            raise WrikeApiError(f"Wrike attach -> {resp.status_code}: {resp.text[:200]}")
        data = resp.json().get("data") or []
        if not data:
            raise WrikeApiError(f"Wrike returned no attachment for {filename}")
        return str(data[0]["id"])

    def delete_attachment(self, attachment_id: str) -> None:
        self._request("DELETE", f"/attachments/{attachment_id}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integrations/test_wrike_client.py -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/integrations/wrike_client.py tests/integrations/test_wrike_client.py
git commit -m "feat(wrike): client methods for spaces, projects, and attachments"
```

---

### Task 2: `wrike_projects` mapping table (schema v9) + repo

**Files:**
- Create: `src/teams_transcriber/storage/schema_v9.py`
- Modify: `src/teams_transcriber/storage/wrike.py` (add `WrikeProjectRow` + `WrikeProjectRepo`)
- Modify: `src/teams_transcriber/storage/__init__.py` (register `SCHEMA_V9`, export the new names)
- Test: `tests/storage/test_wrike_repos.py`

**Interfaces:**
- Consumes: `Migration`, the migration list in `build_database`.
- Produces:
  - `@dataclass WrikeProjectRow: recording_id: int; project_id: str; permalink: str | None; attachment_id: str | None; notes_comment_id: str | None; created_at: str; last_pushed_at: str`
  - `WrikeProjectRepo(db)` with `get(recording_id) -> WrikeProjectRow | None`, `upsert(recording_id, *, project_id, permalink=None) -> None` (sets created_at on first insert, always bumps last_pushed_at), `set_attachment(recording_id, attachment_id)`, `set_notes_comment(recording_id, comment_id)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/storage/test_wrike_repos.py` (reuse its `build_database` + recording-seeding pattern):

```python
def test_wrike_project_roundtrip_and_idempotent_upsert(db_with_recording):
    from teams_transcriber.storage import WrikeProjectRepo
    db, rid = db_with_recording
    repo = WrikeProjectRepo(db)
    assert repo.get(rid) is None
    repo.upsert(rid, project_id="prj1", permalink="https://w/open.htm?id=1")
    row = repo.get(rid)
    assert row.project_id == "prj1" and row.permalink.endswith("id=1")
    first_created = row.created_at
    repo.set_attachment(rid, "att5")
    repo.set_notes_comment(rid, "cmt7")
    repo.upsert(rid, project_id="prj1")           # re-push
    row2 = repo.get(rid)
    assert row2.attachment_id == "att5" and row2.notes_comment_id == "cmt7"
    assert row2.created_at == first_created        # created_at preserved
    assert row2.last_pushed_at >= first_created    # bumped


def test_wrike_project_cascades_on_recording_delete(db_with_recording):
    from teams_transcriber.storage import RecordingRepo, WrikeProjectRepo
    db, rid = db_with_recording
    WrikeProjectRepo(db).upsert(rid, project_id="prj1")
    RecordingRepo(db).delete(rid)
    assert WrikeProjectRepo(db).get(rid) is None
```

If `db_with_recording` doesn't exist in that file, add it mirroring the module's existing db construction (`build_database` + a `RecordingRepo(db).create(...)`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/storage/test_wrike_repos.py -v -k project`
Expected: FAIL (`ImportError: cannot import name 'WrikeProjectRepo'`).

- [ ] **Step 3: Implement**

`schema_v9.py`:

```python
"""v9: wrike_projects — maps a recording to its Wrike project for idempotent
re-push (project + transcript attachment + notes comment ids)."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS = (
    """
    CREATE TABLE wrike_projects (
        recording_id     INTEGER PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
        project_id       TEXT NOT NULL,
        permalink        TEXT,
        attachment_id    TEXT,
        notes_comment_id TEXT,
        created_at       TEXT NOT NULL,
        last_pushed_at   TEXT NOT NULL
    )
    """,
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V9 = Migration(version=9, name="add wrike_projects", apply=_apply)
```

Add to `storage/wrike.py`:

```python
@dataclass(slots=True)
class WrikeProjectRow:
    recording_id: int
    project_id: str
    permalink: str | None
    attachment_id: str | None
    notes_comment_id: str | None
    created_at: str
    last_pushed_at: str


class WrikeProjectRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, recording_id: int) -> WrikeProjectRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT recording_id, project_id, permalink, attachment_id, "
                "notes_comment_id, created_at, last_pushed_at "
                "FROM wrike_projects WHERE recording_id = ?",
                (recording_id,),
            ).fetchone()
        return None if row is None else WrikeProjectRow(*row)

    def upsert(self, recording_id: int, *, project_id: str, permalink: str | None = None) -> None:
        now = _now_utc()
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO wrike_projects (recording_id, project_id, permalink, "
                "created_at, last_pushed_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(recording_id) DO UPDATE SET "
                "project_id=excluded.project_id, "
                "permalink=COALESCE(excluded.permalink, wrike_projects.permalink), "
                "last_pushed_at=excluded.last_pushed_at",
                (recording_id, project_id, permalink, now, now),
            )
            conn.commit()

    def set_attachment(self, recording_id: int, attachment_id: str | None) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE wrike_projects SET attachment_id=? WHERE recording_id=?",
                (attachment_id, recording_id),
            )
            conn.commit()

    def set_notes_comment(self, recording_id: int, comment_id: str) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE wrike_projects SET notes_comment_id=? WHERE recording_id=?",
                (comment_id, recording_id),
            )
            conn.commit()
```

`storage/__init__.py`: import + register `SCHEMA_V9` after `SCHEMA_V8`; import + export `WrikeProjectRepo`, `WrikeProjectRow`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/storage -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/storage/schema_v9.py src/teams_transcriber/storage/wrike.py src/teams_transcriber/storage/__init__.py tests/storage/test_wrike_repos.py
git commit -m "feat(storage): wrike_projects mapping table (schema v9)"
```

---

### Task 3: Pure Markdown builders — description + transcript

**Files:**
- Create: `src/teams_transcriber/integrations/wrike_project_body.py`
- Test: `tests/integrations/test_wrike_project_body.py`

**Interfaces:**
- Consumes: `Summary` (has `.summary: str`, `.key_decisions: list[str]`), `TranscriptSegment` (has `.start_ms: int`, `.channel: Channel`, `.text: str`; `Channel` is a StrEnum ME/OTHERS).
- Produces:
  - `build_description(summary: Summary) -> str` — the summary body, then (if any) a `\n\n## Key decisions\n` section with `- ` bullets. Returns just the summary if no decisions.
  - `build_transcript_md(segments: list[TranscriptSegment]) -> str` — a `# Transcript` heading then one line per segment: `**ME** 00:12 text` / `**OTHERS** 00:15 text` (mm:ss from start_ms). Empty list → `# Transcript\n\n_(no transcript)_`.

- [ ] **Step 1: Write the failing tests**

`tests/integrations/test_wrike_project_body.py`:

```python
from __future__ import annotations

from teams_transcriber.integrations.wrike_project_body import (
    build_description, build_transcript_md,
)
from teams_transcriber.storage.models import Channel, Summary, TranscriptSegment


def _summary(**kw):
    base = dict(
        recording_id=1, title="T", one_line="one", summary="The summary body.",
        key_decisions=[], my_todos=[], action_items_others=[], follow_ups=[],
        topics=[], generated_at="2026-07-24T00:00:00+00:00", model_used="m",
    )
    base.update(kw)
    return Summary(**base)


def test_description_without_decisions_is_just_summary():
    assert build_description(_summary()) == "The summary body."


def test_description_appends_key_decisions_section():
    out = build_description(_summary(key_decisions=["Ship Friday", "Use SQLite"]))
    assert out.startswith("The summary body.")
    assert "## Key decisions" in out
    assert "- Ship Friday" in out and "- Use SQLite" in out


def test_transcript_md_formats_channel_and_timestamp():
    segs = [
        TranscriptSegment(id=None, recording_id=1, start_ms=12000, end_ms=13000,
                          channel=Channel.ME, text="hello"),
        TranscriptSegment(id=None, recording_id=1, start_ms=75000, end_ms=76000,
                          channel=Channel.OTHERS, text="hi back"),
    ]
    md = build_transcript_md(segs)
    assert md.startswith("# Transcript")
    assert "**ME** 00:12 hello" in md
    assert "**OTHERS** 01:15 hi back" in md


def test_transcript_md_empty():
    assert "no transcript" in build_transcript_md([]).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integrations/test_wrike_project_body.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement**

```python
"""Pure Markdown builders for the Wrike project export (no client, no db)."""

from __future__ import annotations

from teams_transcriber.storage.models import Summary, TranscriptSegment


def build_description(summary: Summary) -> str:
    body = summary.summary or ""
    if summary.key_decisions:
        lines = "\n".join(f"- {d}" for d in summary.key_decisions)
        return f"{body}\n\n## Key decisions\n{lines}"
    return body


def _mmss(ms: int) -> str:
    total = max(0, ms // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


def build_transcript_md(segments: list[TranscriptSegment]) -> str:
    if not segments:
        return "# Transcript\n\n_(no transcript)_"
    lines = [
        f"**{s.channel.value.upper()}** {_mmss(s.start_ms)} {s.text}"
        for s in segments
    ]
    return "# Transcript\n\n" + "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integrations/test_wrike_project_body.py -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/integrations/wrike_project_body.py tests/integrations/test_wrike_project_body.py
git commit -m "feat(wrike): markdown builders for project description and transcript"
```

---

### Task 4: The orchestrator — `export_recording`

**Files:**
- Create: `src/teams_transcriber/integrations/wrike_project_export.py`
- Test: `tests/integrations/test_wrike_project_export.py`

**Interfaces:**
- Consumes: `WrikeProjectRepo` (Task 2), `WrikeTaskRepo`/`WrikeTaskRow` (existing), `build_description`/`build_transcript_md` (Task 3), `SummaryRepo`, `RecordingRepo`, `TranscriptRepo`; a client Protocol with `create_project`, `update_project`, `upload_attachment`, `delete_attachment`, `create_task(folder_id, payload)`, `create_comment(*, entity_type, entity_id, text)`.
- Produces:
  - `@dataclass ExportReport: project_id: str | None; permalink: str | None; created_tasks: int; updated: bool; failures: list[str]`
  - `export_recording(db, client, recording_id: int, *, parent_id: str, assignees: dict[int, str | None]) -> ExportReport` where `assignees` maps an action_items_others index → Wrike contact id (or None). Task order: my_todos, then action_items_others (assigned), then follow_ups. Each task is recorded in `wrike_tasks` with `kind` in `{"my", "other", "follow"}` and `todo_index` = its list index; a task already present (by kind+index) is skipped (idempotent re-push). Notes comment posted once (guarded by `notes_comment_id`). Transcript attachment replaced on re-push (delete old id, upload new). Each of the four sub-steps (describe, attach, tasks, notes) is wrapped so a failure appends to `failures` and the rest still run; what succeeded is persisted.

- [ ] **Step 1: Write the failing tests**

`tests/integrations/test_wrike_project_export.py` — build a `FakeClient` recording calls and returning canned ids, seed a recording+summary+transcript via the same repo calls used in `tests/phone_sync/test_library_export.py` / `tests/ui/test_summary_pane.py` (read one for the seeding pattern), against a `build_database` db. Write these in full:

```python
# FakeClient: create_project -> {"id": "prjN", "permalink": "https://w/open.htm?id=N"};
# update_project -> {}; upload_attachment -> "attN" (records (entity, filename, content));
# delete_attachment records the id; create_task -> {"id": "tskN"} (records folder_id+payload);
# create_comment -> "cmtN" (records text). Counters make ids unique.

def test_first_export_creates_project_tasks_attachment_and_notes(db_seeded): ...
    # recording with summary(my_todos=2, follow_ups=1, action_items_others=1 who="Sam"),
    # manual_notes="call Bob", one transcript segment.
    # export_recording(db, fake, rid, parent_id="P", assignees={0: "c-sam"})
    # -> report.project_id == "prj1"; report.created_tasks == 4;
    #    fake saw create_project(parent="P", description contains summary+decisions);
    #    upload_attachment(entity="prj1", filename endswith ".md", content has "# Transcript");
    #    the action_other task payload carries responsibles=["c-sam"];
    #    create_comment(entity_type="folder", entity_id="prj1", text has "call Bob");
    #    WrikeProjectRepo.get(rid) has project_id + attachment_id + notes_comment_id;
    #    WrikeTaskRepo.list_for_recording(rid) has 4 rows.

def test_second_export_updates_not_duplicates(db_seeded): ...
    # run export twice. Second run: report.updated is True, created_tasks == 0
    # (all already mapped), fake saw update_project (not a 2nd create_project),
    # delete_attachment(old id) then a fresh upload_attachment, and NO 2nd create_comment.

def test_partial_failure_records_success_and_resumes(db_seeded): ...
    # FakeClient.create_task raises on the FIRST call only. First export:
    # failures non-empty, project created, SOME tasks + attachment + notes still done.
    # Second export (client healthy): the previously-failed task is now created,
    # created_tasks accounts for the remainder, no duplicates of the succeeded ones.

def test_new_todo_added_on_repush(db_seeded): ...
    # export; then add a 3rd my_todo to the summary; export again ->
    # created_tasks == 1 (only the new one), existing tasks untouched.
```

Write each fully following the seeding + FakeClient patterns.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integrations/test_wrike_project_export.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement `wrike_project_export.py`**

```python
"""Create-once, hands-off Wrike project export for one recording.

Pure orchestration against a client Protocol + the db repos. Idempotent:
re-running updates the description, replaces the transcript attachment, adds
only tasks not already mapped, and posts the notes comment once. Per-step
failures are collected; what succeeded is persisted so a re-push resumes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from teams_transcriber.integrations.wrike_project_body import (
    build_description, build_transcript_md,
)
from teams_transcriber.storage.db import Database
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.summaries import SummaryRepo
from teams_transcriber.storage.transcripts import TranscriptRepo
from teams_transcriber.storage.wrike import (
    WrikeProjectRepo, WrikeTaskRepo, WrikeTaskRow,
)

logger = logging.getLogger(__name__)
_TAG_RE = re.compile(r"<[^>]+>")


class _ClientProto(Protocol):
    def create_project(self, parent_id: str, title: str, description: str) -> dict[str, Any]: ...
    def update_project(self, project_id: str, *, description: str) -> dict[str, Any]: ...
    def upload_attachment(self, entity_id: str, filename: str, content: bytes) -> str: ...
    def delete_attachment(self, attachment_id: str) -> None: ...
    def create_task(self, folder_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def create_comment(self, *, entity_type: str, entity_id: str, text: str) -> str: ...


@dataclass(slots=True)
class ExportReport:
    project_id: str | None = None
    permalink: str | None = None
    created_tasks: int = 0
    updated: bool = False
    failures: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _plain(text: str | None) -> str:
    return _TAG_RE.sub("", text or "").strip()


def export_recording(
    db: Database, client: _ClientProto, recording_id: int,
    *, parent_id: str, assignees: dict[int, str | None],
) -> ExportReport:
    report = ExportReport()
    rec = RecordingRepo(db).get(recording_id)
    summary = SummaryRepo(db).get(recording_id)
    if rec is None or summary is None:
        report.failures.append("recording or summary missing")
        return report

    proj_repo = WrikeProjectRepo(db)
    task_repo = WrikeTaskRepo(db)
    existing = proj_repo.get(recording_id)
    title = rec.display_title or summary.title or "Meeting"
    description = build_description(summary)

    # 1. find-or-create project
    if existing is None:
        try:
            folder = client.create_project(parent_id, title, description)
            project_id = str(folder["id"])
            proj_repo.upsert(recording_id, project_id=project_id,
                             permalink=folder.get("permalink"))
            report.project_id = project_id
            report.permalink = folder.get("permalink")
        except Exception as exc:  # noqa: BLE001 — abort cleanly, retryable
            logger.exception("wrike create_project failed for %d", recording_id)
            report.failures.append(f"create project: {exc}")
            return report
    else:
        project_id = existing.project_id
        report.project_id = project_id
        report.permalink = existing.permalink
        report.updated = True
        try:
            client.update_project(project_id, description=description)
            proj_repo.upsert(recording_id, project_id=project_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("wrike update_project failed for %d", recording_id)
            report.failures.append(f"update description: {exc}")

    # 2. transcript attachment (replace on re-push)
    try:
        md = build_transcript_md(TranscriptRepo(db).list_for_recording(recording_id))
        old = proj_repo.get(recording_id)
        if old and old.attachment_id:
            try:
                client.delete_attachment(old.attachment_id)
            except Exception:  # noqa: BLE001 — stale id; upload the fresh one anyway
                logger.warning("could not delete old attachment %s", old.attachment_id)
        att_id = client.upload_attachment(project_id, "transcript.md", md.encode("utf-8"))
        proj_repo.set_attachment(recording_id, att_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("wrike transcript attach failed for %d", recording_id)
        report.failures.append(f"transcript: {exc}")

    # 3. tasks (my_todos, action_items_others[assigned], follow_ups) — add only new
    def _ensure_task(kind: str, index: int, name: str, assignee: str | None) -> None:
        if task_repo.get(recording_id, kind, index) is not None:
            return
        payload: dict[str, Any] = {"title": name}
        if assignee:
            payload["responsibles"] = [assignee]
        try:
            created = client.create_task(project_id, payload)
            task_repo.insert(WrikeTaskRow(
                id=None, recording_id=recording_id, kind=kind, todo_index=index,
                wrike_task_id=str(created["id"]), wrike_folder_id=project_id,
                created_at=_now(), last_synced_done=False, format="task",
                assignee_id=assignee,
            ))
            report.created_tasks += 1
        except Exception as exc:  # noqa: BLE001 — one task fails, others continue
            logger.exception("wrike create_task failed %s/%d", kind, index)
            report.failures.append(f"task {kind}/{index}: {exc}")

    for i, td in enumerate(summary.my_todos):
        _ensure_task("my", i, td.task + (f" (due {td.due})" if td.due else ""), None)
    for j, ai in enumerate(summary.action_items_others):
        title_txt = f"{ai.who}: {ai.task}" if ai.who else ai.task
        _ensure_task("other", j, title_txt, assignees.get(j))
    for k, f in enumerate(summary.follow_ups):
        _ensure_task("follow", k, f, None)

    # 4. notes comment (once)
    notes = _plain(rec.manual_notes)
    row = proj_repo.get(recording_id)
    if notes and (row is None or not row.notes_comment_id):
        try:
            cid = client.create_comment(entity_type="folder", entity_id=project_id, text=notes)
            proj_repo.set_notes_comment(recording_id, cid)
        except Exception as exc:  # noqa: BLE001
            logger.exception("wrike notes comment failed for %d", recording_id)
            report.failures.append(f"notes: {exc}")

    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integrations/test_wrike_project_export.py -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/integrations/wrike_project_export.py tests/integrations/test_wrike_project_export.py
git commit -m "feat(wrike): create-once project export orchestrator"
```

---

### Task 5: Destination picker dialog

**Files:**
- Create: `src/teams_transcriber/ui/wrike_destination_picker.py`
- Test: `tests/ui/test_wrike_destination_picker.py`

**Interfaces:**
- Consumes: `FramelessWindowMixin`, `TitleBar`, `scrim.exec_modal`, `labels`. A client with `list_spaces() -> list[dict]` and `list_folders() -> list[dict]` (folders carry `id`, `title`, and a `scope`/`space` relationship — but Wrike's `/folders` returns all folders; filter by the chosen space's `childIds` if present, else show all as a flat pick). To stay testable without a live client, the dialog takes **already-fetched** `spaces: list[dict]` and `folders_by_space: dict[str, list[dict]]` (the App's worker fetches them; the dialog is pure UI).
- Produces: `WrikeDestinationPicker(*, spaces, folders_by_space, parent=None)` with `.selected -> tuple[str, str] | None` = `(parent_id, label)` after accept. Layout: a spaces list on the left; selecting a space shows its folders on the right with a "(space root)" first entry (selecting it returns the space id + space title); selecting a folder returns the folder id + `"Space / Folder"` label.

- [ ] **Step 1: Write the failing tests**

`tests/ui/test_wrike_destination_picker.py`:

```python
from __future__ import annotations

from teams_transcriber.ui.wrike_destination_picker import WrikeDestinationPicker

_SPACES = [{"id": "sp1", "title": "Team"}, {"id": "sp2", "title": "Personal"}]
_FOLDERS = {"sp1": [{"id": "f1", "title": "Meetings"}], "sp2": []}


def test_selecting_space_root_returns_space(qapp):
    dlg = WrikeDestinationPicker(spaces=_SPACES, folders_by_space=_FOLDERS)
    dlg._select_space("sp1")
    dlg._select_target(None)            # (space root)
    assert dlg.selected == ("sp1", "Team")


def test_selecting_folder_returns_folder_with_path_label(qapp):
    dlg = WrikeDestinationPicker(spaces=_SPACES, folders_by_space=_FOLDERS)
    dlg._select_space("sp1")
    dlg._select_target("f1")
    assert dlg.selected == ("f1", "Team / Meetings")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_wrike_destination_picker.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement**

Build a frameless themed `QDialog` mirroring `wrike_folder_picker.py`'s structure (read it before deleting in Task 8 — reuse its frame/title-bar/scrim scaffolding). Two `QListWidget`s side by side; `_select_space(space_id)` populates the right list with a `"(space root)"` item (data=None) then the space's folders; `_select_target(target_id)` sets `self.selected` = `(space_id, space_title)` when `target_id is None` else `(target_id, f"{space_title} / {folder_title}")`; a Choose button accepts. Keep the two `_select_*` methods as the tested seam (the UI list signals call them). `selected` defaults to `None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_wrike_destination_picker.py -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/wrike_destination_picker.py tests/ui/test_wrike_destination_picker.py
git commit -m "feat(ui): Wrike destination picker (space or folder)"
```

---

### Task 6: Settings — project-export toggle + destination

**Files:**
- Modify: `src/teams_transcriber/ui/settings_dialog.py` (`_build_integrations_tab`, `_on_accept`)
- Test: `tests/ui/test_settings_integrations_tab.py`

**Interfaces:**
- Consumes: `WrikeDestinationPicker` (Task 5), `WrikeClient.list_spaces`/`list_folders` (Task 1), `scrim.exec_modal`, `labels.make_selectable`.
- Produces: `self.wrike_project_export_cb: QCheckBox` ("Create a Wrike project for each meeting automatically"); a "Choose destination…" button that fetches spaces/folders in a worker and opens the picker; a read-only `self.wrike_dest_label` showing `integrations.wrike_parent_label` (or "None chosen"). Persist in `_on_accept`: `integrations.wrike_project_export_enabled`, `integrations.wrike_parent_id`, `integrations.wrike_parent_label`. **Remove** the old `wrike_enable_cb` ("auto-send todos") — but keep `wrike_llm_assignee_cb` (assignees still used). Keep the token + Test-connection rows unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_settings_integrations_tab.py` (reuse its dialog fixture):

```python
def test_project_export_toggle_and_destination_persist(...):
    # build dialog; set wrike_project_export_cb checked; simulate a chosen
    # destination by setting dlg._chosen_parent = ("f1", "Team / Meetings")
    # (the attribute the picker callback writes); _on_accept; reload settings ->
    # integrations.wrike_project_export_enabled True,
    # wrike_parent_id == "f1", wrike_parent_label == "Team / Meetings".


def test_old_auto_send_todos_checkbox_is_gone(...):
    import inspect
    from teams_transcriber.ui import settings_dialog as sd
    src = inspect.getsource(sd.SettingsDialog._build_integrations_tab)
    assert "wrike_enable_cb" not in src        # replaced by project-export
    assert "wrike_project_export_cb" in src
```

Complete the first test's `...` from the file's existing fixture pattern.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_settings_integrations_tab.py -v -k "project_export or auto_send"`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `_build_integrations_tab`: after the token + Test-connection rows, remove the `wrike_enable_cb` block and add:
- `self.wrike_project_export_cb = QCheckBox("Create a Wrike project for each meeting automatically")` checked from `integrations.wrike_project_export_enabled`.
- `self._chosen_parent: tuple[str, str] | None = (parent_id, parent_label)` seeded from settings (or None).
- `self.wrike_dest_label = make_selectable(QLabel(parent_label or "No destination chosen"))`.
- A "Choose destination…" secondary button → `self._choose_wrike_destination()`: reads the token, in a worker thread calls `list_spaces()` + `list_folders()`, groups folders by space (best-effort: a folder belongs to a space if the space's `childIds` contains it; fall back to showing all folders under every space), hops back to open `WrikeDestinationPicker` via `exec_modal`; on accept sets `self._chosen_parent` and updates `self.wrike_dest_label`. (Mirror the existing `_wrike_test_connection` worker+hop pattern.)

In `_on_accept`, alongside the other `integrations` writes:
```python
        integ = s._raw.setdefault("integrations", {})
        integ["wrike_project_export_enabled"] = self.wrike_project_export_cb.isChecked()
        if self._chosen_parent is not None:
            integ["wrike_parent_id"] = self._chosen_parent[0]
            integ["wrike_parent_label"] = self._chosen_parent[1]
```
Remove the `wrike_enabled` write. Keep the `wrike_llm_assignee_fallback` write.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_settings_integrations_tab.py -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/settings_dialog.py tests/ui/test_settings_integrations_tab.py
git commit -m "feat(ui): Settings project-export toggle and Wrike destination"
```

---

### Task 7: App wiring — SummaryReady + manual push → orchestrator

**Files:**
- Modify: `src/teams_transcriber/ui/app.py`
- Test: `tests/ui/test_app_wrike_project.py` (new)

**Interfaces:**
- Consumes: `export_recording`/`ExportReport` (Task 4), `WrikeClient` (Task 1), `suggest_assignees`+`Contact` (existing), `WrikeSyncRepo` (pending queue, existing), `show_in_app_toast`, `_anthropic_key`, `settings._raw["integrations"]`.
- Produces:
  - `App._wrike_project_enabled() -> bool` (token present + `wrike_project_export_enabled` + a `wrike_parent_id` set).
  - `App._on_summary_ready_wrike(evt)` — REPLACED: if `_wrike_project_enabled()`, spawn `_wrike_export_worker(recording_id)`.
  - `App._wrike_export_worker(recording_id)` — worker thread: build a `WrikeClient`; resolve assignees for `action_items_others` via `suggest_assignees` (contacts from `list_contacts`, meeting summary, `_anthropic_key`, `ai_model`, gated on `wrike_llm_assignee_fallback`); call `export_recording(self.db, client, recording_id, parent_id=..., assignees=...)`; on failure or `report.failures` mark `WrikeSyncRepo` pending/failed; hop a toast (success → "Synced to Wrike" + "Open in Wrike" opening `report.permalink`; failure → "Wrike sync failed" + Retry re-invoking the worker) to the main thread.
  - The SummaryPane "Send to Wrike" button (`wrike_sync_requested` signal) now calls `_wrike_export_worker` directly (manual re-send).
  - The startup pending-retry toast (existing `_wrike_pick_pending`) now retries via `_wrike_export_worker`.
  - **Removed** (moved to Task 8's deletions but stop calling them here): `_on_todo_state_changed`'s `_wrike_close_loop_sync` call, `_on_master_todo_toggled`'s close-loop call, and the planner-launch methods.

- [ ] **Step 1: Write the failing tests**

`tests/ui/test_app_wrike_project.py` (mirror `tests/ui/test_app_wrike_close_loop.py`'s `App.__new__` + `SimpleNamespace` pattern):

```python
def test_summary_ready_spawns_export_when_enabled(...):
    # App.__new__(App); stub settings with integrations enabled + parent set,
    # keyring token present; monkeypatch _wrike_export_worker to record calls;
    # call _on_summary_ready_wrike(evt(recording_id=5)); assert worker called with 5.


def test_summary_ready_noop_when_disabled(...):
    # export_enabled False -> _wrike_export_worker NOT called.


def test_wrike_project_enabled_requires_token_toggle_and_parent(...):
    # table-test the three conditions of _wrike_project_enabled.
```

Complete from the fixture pattern; keep them at the App-method level (no real network — monkeypatch the worker / _anthropic_key / settings).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_app_wrike_project.py -v`
Expected: FAIL (methods missing / old behavior).

- [ ] **Step 3: Implement**

Replace `_on_summary_ready_wrike`, `_wrike_open_picker`, `_wrike_open_planner`, `_wrike_planner_show`, `_wrike_run_plan`, `_wrike_open_planner_kwargs` with the new `_wrike_project_enabled` / `_on_summary_ready_wrike` / `_wrike_export_worker`. Point `wrike_sync_requested` and the pending-retry toast at `_wrike_export_worker`. In `_on_todo_state_changed` and `_on_master_todo_toggled`, drop the `_wrike_close_loop_sync(rid)` call (keep the history refresh + master reload). Leave `_wrike_close_loop_sync`/`_wrike_apply_close_loop`/`_wrike_close_loop_changes` defined for now (Task 8 deletes them) — but unreferenced.

`_wrike_export_worker` skeleton (fill in mirroring `_wrike_run_plan`'s worker/hop):

```python
    def _wrike_export_worker(self, recording_id: int) -> None:
        import keyring, threading
        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE

        def _worker() -> None:
            from PySide6.QtCore import QTimer
            from teams_transcriber.integrations.wrike_client import WrikeApiError, WrikeClient
            from teams_transcriber.integrations.wrike_project_export import export_recording
            from teams_transcriber.storage.wrike import WrikeSyncRepo
            token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
            parent_id = self.settings._raw.get("integrations", {}).get("wrike_parent_id")
            if not token or not parent_id:
                return
            client = WrikeClient(token=token)
            try:
                assignees = self._resolve_wrike_assignees(recording_id, client)
                report = export_recording(self.db, client, recording_id,
                                          parent_id=parent_id, assignees=assignees)
            except WrikeApiError as exc:
                WrikeSyncRepo(self.db).update(recording_id, status="failed", error_message=str(exc))
                QTimer.singleShot(0, self.window, lambda e=str(exc): show_in_app_toast("Wrike sync failed", e))
                return
            except Exception as exc:
                logger.exception("wrike export crashed for %d", recording_id)
                WrikeSyncRepo(self.db).update(recording_id, status="failed", error_message=str(exc))
                QTimer.singleShot(0, self.window, lambda e=str(exc): show_in_app_toast("Wrike sync failed", e))
                return
            finally:
                client.close()
            if report.failures:
                WrikeSyncRepo(self.db).update(recording_id, status="failed",
                                              error_message="; ".join(report.failures))
                QTimer.singleShot(0, self.window, lambda: show_in_app_toast(
                    "Wrike sync — partial", f"{len(report.failures)} item(s) failed; will retry.",
                    action_label="Retry", action_callback=lambda: self._wrike_export_worker(recording_id)))
            else:
                WrikeSyncRepo(self.db).update(recording_id, status="synced")
                link = report.permalink
                QTimer.singleShot(0, self.window, lambda: show_in_app_toast(
                    "Synced to Wrike", "Project created.",
                    action_label=("Open in Wrike" if link else None),
                    action_callback=((lambda: __import__("webbrowser").open(link)) if link else None)))
        threading.Thread(target=_worker, daemon=True).start()
```

Add `_resolve_wrike_assignees(recording_id, client) -> dict[int, str|None]` extracted from the planner's old resolve phase (contacts → `Contact` objects → `suggest_assignees` over `action_items_others`, gated on `wrike_llm_assignee_fallback` + a present anthropic key). Return `{}` if there are no action-items-others.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_app_wrike_project.py -v` then the full suite once. Fix any existing app tests that asserted the old planner/close-loop wiring (update them to the new behavior).

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py tests/ui/test_app_wrike_project.py tests/ui/
git commit -m "feat(app): auto Wrike project export on SummaryReady; drop close-loop"
```

---

### Task 8: Remove the planner, folder picker, and item-by-item sync

**Files:**
- Delete: `src/teams_transcriber/ui/wrike_sync_planner.py`, `src/teams_transcriber/ui/wrike_folder_picker.py`, `src/teams_transcriber/integrations/wrike_sync.py`, `src/teams_transcriber/integrations/wrike_items.py`
- Delete: `tests/ui/test_wrike_sync_planner.py`, `tests/ui/test_wrike_planner_flow.py`, `tests/ui/test_wrike_folder_picker.py`, `tests/integrations/test_wrike_sync.py`, `tests/integrations/test_wrike_sync_items.py`, `tests/integrations/test_wrike_items.py`
- Modify: `src/teams_transcriber/ui/app.py` (delete now-dead helpers), `src/teams_transcriber/ui/summary_pane.py` (button label if it referenced the planner), `src/teams_transcriber/ui/settings_dialog.py` (drop `wrike_llm_assignee`... keep it — used), `tests/ui/test_app_wrike_close_loop.py` (delete or repoint — close-loop is gone)

**Interfaces:** none new — removal only. The suite must stay green.

- [ ] **Step 1: Grep for every reference, delete top-down**

Run `grep -rn "wrike_sync_planner\|wrike_folder_picker\|WrikeSyncPlanner\|WrikeFolderPicker\|from teams_transcriber.integrations.wrike_sync\|wrike_items\|sync_items\|recording_to_sync_items\|_wrike_close_loop\|_wrike_open_planner\|_wrike_run_plan\|db_kind_to_sync_kind" src tests` and remove every hit: delete the modules + their tests, delete the now-orphaned app.py methods (`_wrike_close_loop_sync`, `_wrike_apply_close_loop`, `_wrike_close_loop_changes`, `_wrike_lru_push` if only the planner used it, `_wrike_picker_load_failed`, `_wrike_planner_show`, `_wrike_run_plan`, `_wrike_open_planner`, `_wrike_open_picker`, `_wrike_open_planner_kwargs`), and any import of the deleted modules.

- [ ] **Step 2: Run the full suite to find stragglers**

Run: `uv run pytest -q`
Expected: green. Any ImportError/AttributeError points at a missed reference — fix it.

- [ ] **Step 3: Lint**

Run: `.venv\Scripts\python.exe -m ruff check src/teams_transcriber/ui/app.py src/teams_transcriber/integrations src/teams_transcriber/ui/settings_dialog.py`
Expected: no new unused-import / undefined-name errors from the removal.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(wrike): remove item-by-item planner, folder picker, sync_items"
```

---

## Final verification (after Task 8)

- [ ] `uv run pytest` — full suite green.
- [ ] **Real-Wrike spike (controller-run, like the MTP spike):** a scratch script with Brian's real token against a throwaway Space — confirm `create_project` yields a project with a working `permalink`, `upload_attachment` accepts the raw-body + `X-File-Name` shape and the `.md` opens in Wrike, `create_task` nests under the project, `create_comment` posts, and a second `export_recording` updates without duplicating. Adjust the client's attachment/project shapes if Wrike disagrees with the documented forms.
- [ ] End-to-end in the app: enable project export, pick a destination, let a real meeting summarize, confirm the Wrike project appears with description + transcript attachment + tasks + notes comment, and the toast's "Open in Wrike" opens it.
- [ ] `git log --oneline` — one conventional commit per task.
