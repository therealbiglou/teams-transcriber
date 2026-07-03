# Phase 12 — Meeting Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A persistent per-meeting Q&A box embedded at the bottom of `SummaryPane` — the user types a question, Claude answers using summary + manual notes + full transcript as context, and the whole conversation persists across sessions.

**Architecture:** New SQLite table `chat_messages` (schema_v5, pure additions, CASCADE-delete with recordings) + Qt-free `chat.py::ask` orchestrator that builds an Anthropic prompt-cached system block from the meeting's context and calls `client.messages.create` with the persisted conversation history + new turn. UI is `ChatCard(QFrame)` slotted into `SummaryPane` when transcript segments exist; App dispatches the send to a daemon thread and hops the reply back via `QTimer.singleShot(0, self.window, ...)` (the same threading pattern as the Wrike integration).

**Tech Stack:** Python 3.11, PySide6, SQLite, `anthropic` SDK (already a dep). Run tests with `& "C:\Users\brian\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest`.

Spec: `docs/superpowers/specs/2026-06-09-phase-12-meeting-chat-design.md`.

> **DB API gotcha (learned in Phase 11):** `Database` exposes `connect()` as a context manager — there is NO `db.conn` attribute. All SQL in `ChatRepo` must go through `with self._db.connect() as conn:`. Mirror `WrikeSyncRepo` / `RecordingRepo`.

> **Qt threading gotcha (learned in Phase 11):** `QTimer.singleShot(0, callable)` called from a non-Qt worker thread creates the timer on the worker (no event loop) and silently never fires. Always use the 3-arg form `QTimer.singleShot(0, self.window, callable)` so the timer lives on the main thread.

---

## File Structure

**Create:**
- `src/teams_transcriber/storage/schema_v5.py` — pure CREATE migration.
- `src/teams_transcriber/storage/chat.py` — `ChatMessage` dataclass + `ChatRepo`.
- `src/teams_transcriber/chat.py` — Qt-free orchestrator + typed errors.
- `src/teams_transcriber/ui/chat_card.py` — `ChatCard` widget.
- `tests/storage/test_schema_v5_migration.py`, `tests/storage/test_chat_repo.py`.
- `tests/test_chat.py`.
- `tests/ui/test_chat_card.py`.

**Modify:**
- `src/teams_transcriber/storage/__init__.py` — register `SCHEMA_V5`, export `ChatMessage` / `ChatRepo`.
- `src/teams_transcriber/ui/summary_pane.py` — new constructor kwarg + signal + chat-card insertion.
- `src/teams_transcriber/ui/app.py` — `_anthropic_key()` getter, pass to `SummaryPane`, wire `chat_send_requested → _on_chat_send`, daemon-thread worker, main-thread hop.

---

## Task 1: schema_v5 + ChatRepo

**Files:**
- Create: `src/teams_transcriber/storage/schema_v5.py`
- Create: `src/teams_transcriber/storage/chat.py`
- Modify: `src/teams_transcriber/storage/__init__.py`
- Test: `tests/storage/test_schema_v5_migration.py`, `tests/storage/test_chat_repo.py`

- [ ] **Step 1: Write the failing migration test**

```python
# tests/storage/test_schema_v5_migration.py
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import build_database
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus,
)
from teams_transcriber.storage.recordings import RecordingRepo


def test_v5_migration_adds_chat_messages_with_cascade(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    with db.connect() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "chat_messages" in names
        conn.execute(
            "INSERT INTO chat_messages (recording_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)", (rec.id, "user", "hi", "2026-06-09T10:00:00Z"),
        )
        conn.execute(
            "INSERT INTO chat_messages (recording_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)", (rec.id, "assistant", "hello", "2026-06-09T10:00:01Z"),
        )
        conn.commit()
    RecordingRepo(db).delete(rec.id)
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
    assert n == 0
    db.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `& "<uv>" run pytest tests/storage/test_schema_v5_migration.py -v`
Expected: FAIL — `no such table: chat_messages`.

- [ ] **Step 3: Implement schema_v5 + register it**

Create `src/teams_transcriber/storage/schema_v5.py`:
```python
"""Schema v5: add chat_messages table for per-meeting Q&A with Claude.

Pure CREATE addition — no existing-table CHECK changes, no rebuild.
ON DELETE CASCADE from recordings so a meeting's chat history disappears
with the meeting. The composite index keeps `list_for_recording(rid)`
ordered by insertion (id) without a separate sort.
"""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE chat_messages (
        id           INTEGER PRIMARY KEY,
        recording_id INTEGER NOT NULL
                     REFERENCES recordings(id) ON DELETE CASCADE,
        role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content      TEXT NOT NULL,
        created_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX chat_messages_recording_idx ON chat_messages (recording_id, id)",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V5 = Migration(version=5, name="add chat_messages", apply=_apply)
```

In `src/teams_transcriber/storage/__init__.py` (read it first), add the import next to the other `schema_v*` imports and append `SCHEMA_V5` to the `ALL_MIGRATIONS` tuple:
```python
from teams_transcriber.storage.schema_v5 import SCHEMA_V5
...
ALL_MIGRATIONS: tuple[Migration, ...] = (SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5)
```
Add `SCHEMA_V5` to `__all__`.

- [ ] **Step 4: Run to verify it passes**

Run: `& "<uv>" run pytest tests/storage/test_schema_v5_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Write failing repo tests**

```python
# tests/storage/test_chat_repo.py
import pytest

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import build_database
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.chat import ChatMessage, ChatRepo


@pytest.fixture
def db_with_recording(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    yield db, rec.id
    db.close()


def test_append_and_list_in_insertion_order(db_with_recording):
    db, rid = db_with_recording
    repo = ChatRepo(db)
    repo.append(rid, "user", "what was decided?")
    repo.append(rid, "assistant", "Ship Friday.")
    repo.append(rid, "user", "and who?")
    msgs = repo.list_for_recording(rid)
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert [m.content for m in msgs] == ["what was decided?", "Ship Friday.", "and who?"]


def test_list_empty_for_unknown_recording(db_with_recording):
    db, _ = db_with_recording
    assert ChatRepo(db).list_for_recording(999_999) == []


def test_clear_removes_all_messages_for_recording(db_with_recording):
    db, rid = db_with_recording
    repo = ChatRepo(db)
    repo.append(rid, "user", "x")
    repo.append(rid, "assistant", "y")
    repo.clear(rid)
    assert repo.list_for_recording(rid) == []


def test_clear_only_affects_its_recording(db_with_recording):
    db, rid = db_with_recording
    rid2 = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T11:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t2", display_title="t2",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    )).id
    assert rid2 is not None
    repo = ChatRepo(db)
    repo.append(rid, "user", "a")
    repo.append(rid2, "user", "b")
    repo.clear(rid)
    rest = repo.list_for_recording(rid2)
    assert [m.content for m in rest] == ["b"]
```

- [ ] **Step 6: Run to verify they fail**

Run: `& "<uv>" run pytest tests/storage/test_chat_repo.py -v`
Expected: FAIL — `ChatRepo` not defined.

- [ ] **Step 7: Implement `storage/chat.py`**

```python
"""Repo for the chat_messages table (schema v5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from teams_transcriber.storage.db import Database


@dataclass(slots=True)
class ChatMessage:
    id: int | None
    recording_id: int
    role: str             # 'user' | 'assistant'
    content: str
    created_at: str


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ChatRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list_for_recording(self, recording_id: int) -> list[ChatMessage]:
        with self._db.connect() as conn:
            cur = conn.execute(
                "SELECT id, recording_id, role, content, created_at "
                "FROM chat_messages WHERE recording_id = ? ORDER BY id",
                (recording_id,),
            )
            rows = cur.fetchall()
        return [
            ChatMessage(
                id=r[0], recording_id=r[1], role=r[2],
                content=r[3], created_at=r[4],
            )
            for r in rows
        ]

    def append(self, recording_id: int, role: str, content: str) -> int:
        with self._db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO chat_messages (recording_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (recording_id, role, content, _now_iso()),
            )
            conn.commit()
        return cur.lastrowid

    def clear(self, recording_id: int) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "DELETE FROM chat_messages WHERE recording_id = ?",
                (recording_id,),
            )
            conn.commit()
```

Export from `storage/__init__.py`:
```python
from teams_transcriber.storage.chat import ChatMessage, ChatRepo
```
Add to `__all__`.

- [ ] **Step 8: Run to verify they pass + full suite**

Run: `& "<uv>" run pytest tests/storage/test_chat_repo.py tests/storage/test_schema_v5_migration.py -v`
Expected: PASS.
Run: `& "<uv>" run pytest -q`
Expected: all green (existing 402 + new tests).

- [ ] **Step 9: Commit**

```bash
git add src/teams_transcriber/storage/schema_v5.py src/teams_transcriber/storage/chat.py src/teams_transcriber/storage/__init__.py tests/storage/test_schema_v5_migration.py tests/storage/test_chat_repo.py
git commit -m "feat(storage): schema_v5 + ChatRepo (chat_messages table)"
```

---

## Task 2: `chat.py` orchestrator (Qt-free, with prompt caching)

**Files:**
- Create: `src/teams_transcriber/chat.py`
- Test: `tests/test_chat.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat.py
from dataclasses import dataclass
from typing import Any

import pytest

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    Channel, RecordingRepo, RecordingSource, RecordingStatus, SummaryRepo,
    TranscriptRepo, build_database,
)
from teams_transcriber.storage.chat import ChatRepo
from teams_transcriber.storage.models import (
    Recording, Summary, TranscriptSegment,
)
from teams_transcriber.chat import (
    ChatApiError, ChatAuthError, ChatTokenLimitError,
    TRANSCRIPT_CHAR_CEILING, ask,
)


# --- Anthropic SDK fakes ----------------------------------------------

@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Response:
    content: list[_Block]


class _FakeMessages:
    def __init__(self, reply: str = "OK", capture: dict | None = None,
                 exc: Exception | None = None) -> None:
        self._reply = reply
        self._capture = capture if capture is not None else {}
        self._exc = exc

    def create(self, **kwargs: Any) -> _Response:
        self._capture.update(kwargs)
        if self._exc is not None:
            raise self._exc
        return _Response(content=[_Block(text=self._reply)])


class _FakeClient:
    def __init__(self, reply: str = "OK", capture: dict | None = None,
                 exc: Exception | None = None) -> None:
        self.messages = _FakeMessages(reply=reply, capture=capture, exc=exc)


@pytest.fixture
def env(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    yield paths, db
    db.close()


def _make_recording(db, *, with_summary: bool = True,
                    with_transcript: bool = True,
                    transcript_text: str = "Brian: hello there\nJennifer: hi") -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="Potter Sync",
        display_title="Potter Sync", audio_path=None, audio_deleted_at=None,
        duration_ms=1500, status=RecordingStatus.DONE,
        error_message=None, manual_notes="<p>important: floor plan is rev 3</p>",
    ))
    if with_summary:
        SummaryRepo(db).upsert(Summary(
            recording_id=rec.id, title="Potter Sync", one_line=None,
            summary="discussed booth", key_decisions=["Ship Friday"],
            my_todos=[], action_items_others=[], follow_ups=[], topics=[],
            generated_at="2026-06-09T11:00:00+00:00", model_used="m",
        ))
    if with_transcript:
        TranscriptRepo(db).append_many([
            TranscriptSegment(
                id=None, recording_id=rec.id, start_ms=0, end_ms=2000,
                channel=Channel.ME, text=transcript_text,
            ),
        ])
    return rec.id


def test_ask_persists_user_and_assistant_turns(env):
    _, db = env
    rid = _make_recording(db)
    reply = ask(
        db, rid, "what time did they meet?",
        api_key="k", model="claude-sonnet-4-6",
        anthropic_client_factory=lambda _k: _FakeClient(reply="They met at 10am."),
    )
    assert reply == "They met at 10am."
    msgs = ChatRepo(db).list_for_recording(rid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "They met at 10am."


def test_ask_includes_summary_notes_and_transcript_in_cached_system_block(env):
    _, db = env
    rid = _make_recording(db)
    captured: dict = {}
    ask(
        db, rid, "q?",
        api_key="k", model="claude-sonnet-4-6",
        anthropic_client_factory=lambda _k: _FakeClient(reply="ans", capture=captured),
    )
    system = captured["system"]
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    text = system[0]["text"]
    assert "Potter Sync" in text
    assert "discussed booth" in text
    assert "Ship Friday" in text
    assert "floor plan is rev 3" in text       # notes stripped of HTML
    assert "hello there" in text


def test_ask_history_is_appended_to_messages(env):
    _, db = env
    rid = _make_recording(db)
    ChatRepo(db).append(rid, "user", "first?")
    ChatRepo(db).append(rid, "assistant", "first answer")
    captured: dict = {}
    ask(
        db, rid, "second?",
        api_key="k", model="claude-sonnet-4-6",
        anthropic_client_factory=lambda _k: _FakeClient(reply="second answer", capture=captured),
    )
    msgs = captured["messages"]
    # prior 2 + new user = 3
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1]["content"] == "second?"


def test_ask_uses_configured_model_and_caps_max_tokens(env):
    _, db = env
    rid = _make_recording(db)
    captured: dict = {}
    ask(
        db, rid, "q?",
        api_key="k", model="claude-haiku-4-5",
        anthropic_client_factory=lambda _k: _FakeClient(reply="a", capture=captured),
    )
    assert captured["model"] == "claude-haiku-4-5"
    assert captured["max_tokens"] == 1024


def test_ask_raises_auth_on_anthropic_401(env):
    _, db = env
    rid = _make_recording(db)
    import anthropic
    class _401(anthropic.AuthenticationError):
        def __init__(self):
            pass    # avoid the real ctor's network response
    with pytest.raises(ChatAuthError):
        ask(
            db, rid, "q?", api_key="bad", model="x",
            anthropic_client_factory=lambda _k: _FakeClient(exc=_401()),
        )


def test_ask_raises_token_limit_when_transcript_exceeds_ceiling(env):
    _, db = env
    huge = "x" * (TRANSCRIPT_CHAR_CEILING + 10)
    rid = _make_recording(db, transcript_text=huge)
    with pytest.raises(ChatTokenLimitError):
        ask(
            db, rid, "q?", api_key="k", model="x",
            anthropic_client_factory=lambda _k: _FakeClient(reply="never"),
        )


def test_ask_persists_user_even_when_api_fails(env):
    """User turn is logged before the API call so retries / errors don't
    lose what the user asked."""
    _, db = env
    rid = _make_recording(db)
    with pytest.raises(ChatApiError):
        ask(
            db, rid, "lost question",
            api_key="k", model="x",
            anthropic_client_factory=lambda _k: _FakeClient(exc=RuntimeError("boom")),
        )
    msgs = ChatRepo(db).list_for_recording(rid)
    assert [m.role for m in msgs] == ["user"]
    assert msgs[0].content == "lost question"


def test_ask_omits_empty_sections(env):
    """No summary + no notes + non-empty transcript: system block still works."""
    _, db = env
    rid = _make_recording(db, with_summary=False)
    captured: dict = {}
    ask(
        db, rid, "q?", api_key="k", model="x",
        anthropic_client_factory=lambda _k: _FakeClient(reply="a", capture=captured),
    )
    text = captured["system"][0]["text"]
    assert "# Summary" not in text
    assert "# Decisions" not in text
    assert "# Transcript" in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `& "<uv>" run pytest tests/test_chat.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `chat.py`**

```python
"""Per-meeting chat with Claude.

`ask(db, recording_id, user_text, ...)` persists the user turn, calls Claude
with a prompt-cached system block (summary + manual notes + full transcript)
+ the persisted conversation history, persists the assistant reply, returns
it. Qt-free.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

import anthropic

from teams_transcriber.storage.chat import ChatRepo
from teams_transcriber.storage.db import Database
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.summaries import SummaryRepo
from teams_transcriber.storage.transcripts import TranscriptRepo
from teams_transcriber.storage.models import Channel, TranscriptSegment

logger = logging.getLogger(__name__)

TRANSCRIPT_CHAR_CEILING = 600_000   # matches the summarizer's existing guard
_MAX_TOKENS = 1024
_TAG_RE = re.compile(r"<[^>]+>")


class ChatApiError(RuntimeError):
    """Generic chat failure (network, 5xx, etc.)."""


class ChatAuthError(ChatApiError):
    """401/403 — Anthropic key missing or invalid."""


class ChatTokenLimitError(ChatApiError):
    """Transcript exceeded the chat-input ceiling."""


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    return _TAG_RE.sub("", s).strip()


def _fmt_segment(seg: TranscriptSegment) -> str:
    total = max(0, seg.start_ms // 1000)
    ts = f"{total // 60:02d}:{total % 60:02d}"
    speaker = "ME" if seg.channel == Channel.ME else "OTHERS"
    return f"[{ts}] {speaker}: {seg.text}"


def _build_system_text(db: Database, recording_id: int) -> str:
    rec = RecordingRepo(db).get(recording_id)
    summary = SummaryRepo(db).get(recording_id)
    segments = TranscriptRepo(db).list_for_recording(recording_id)
    transcript_text = "\n".join(_fmt_segment(s) for s in segments)
    if len(transcript_text) > TRANSCRIPT_CHAR_CEILING:
        raise ChatTokenLimitError(
            f"Transcript is {len(transcript_text)} characters — over the "
            f"{TRANSCRIPT_CHAR_CEILING}-character limit. Split the meeting "
            "or shorten the transcript to chat about it."
        )

    parts: list[str] = ["You are answering questions about a meeting."]
    title = (rec.display_title if rec else None) or (
        summary.title if summary else None) or "Meeting"
    started_at = rec.started_at if rec else ""
    parts.append(f"# Meeting\n{title}    started {started_at}")
    if summary is not None:
        if summary.summary:
            parts.append(f"# Summary\n{summary.summary}")
        if summary.key_decisions:
            parts.append("# Decisions\n" + "\n".join(
                f"- {d}" for d in summary.key_decisions
            ))
        if summary.my_todos:
            parts.append("# My todos\n" + "\n".join(
                f"- {t.task}" + (f" (due {t.due})" if t.due else "")
                for t in summary.my_todos
            ))
        if summary.action_items_others:
            parts.append("# Action items for others\n" + "\n".join(
                f"- {a.who}: {a.task}" + (f" (due {a.due})" if a.due else "")
                for a in summary.action_items_others
            ))
    notes = _strip_html(rec.manual_notes if rec else None)
    if notes:
        parts.append(f"# Manual notes\n{notes}")
    parts.append(f"# Transcript\n{transcript_text}")
    return "\n\n".join(parts)


def _default_client_factory(api_key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key)


def ask(
    db: Database,
    recording_id: int,
    user_text: str,
    *,
    api_key: str,
    model: str,
    anthropic_client_factory: Callable[[str], Any] | None = None,
) -> str:
    """Persist the user turn, call Claude, persist the reply, return it."""
    repo = ChatRepo(db)
    # Persist FIRST so a failed API call still records what the user asked.
    repo.append(recording_id, "user", user_text)

    # Build context. May raise ChatTokenLimitError.
    system_text = _build_system_text(db, recording_id)
    history = repo.list_for_recording(recording_id)
    messages = [{"role": m.role, "content": m.content} for m in history]

    factory = anthropic_client_factory or _default_client_factory
    client = factory(api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=[{
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=messages,
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise ChatAuthError(str(exc) or "Anthropic auth failed") from exc
    except anthropic.APIError as exc:
        raise ChatApiError(str(exc) or "Anthropic API error") from exc
    except Exception as exc:
        raise ChatApiError(str(exc) or "chat request failed") from exc

    reply_text = ""
    for block in response.content or []:
        if getattr(block, "type", None) == "text":
            reply_text += block.text
    reply_text = reply_text.strip() or "(empty reply)"
    repo.append(recording_id, "assistant", reply_text)
    return reply_text
```

- [ ] **Step 4: Run to verify they pass**

Run: `& "<uv>" run pytest tests/test_chat.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/chat.py tests/test_chat.py
git commit -m "feat(chat): per-meeting chat orchestrator with cached context"
```

---

## Task 3: `ChatCard` widget

**Files:**
- Create: `src/teams_transcriber/ui/chat_card.py`
- Test: `tests/ui/test_chat_card.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/ui/test_chat_card.py
from PySide6.QtWidgets import QLineEdit, QPushButton, QTextEdit
from teams_transcriber.storage.chat import ChatMessage
from teams_transcriber.ui.chat_card import ChatCard


def _msg(role: str, content: str, mid: int = 1) -> ChatMessage:
    return ChatMessage(id=mid, recording_id=10, role=role,
                       content=content, created_at="x")


def test_empty_history_shows_placeholder(qapp):
    card = ChatCard(recording_id=10, history=[])
    txt = card._placeholder.text().lower()
    assert "ask" in txt or "chat" in txt


def test_history_renders_user_and_assistant_bubbles(qapp):
    card = ChatCard(
        recording_id=10,
        history=[
            _msg("user", "what was decided?"),
            _msg("assistant", "Ship Friday.", mid=2),
        ],
    )
    bubble_texts = [w.toPlainText() if isinstance(w, QTextEdit) else w.text()
                    for w in card._message_container.findChildren(object)
                    if hasattr(w, "text") or isinstance(w, QTextEdit)]
    joined = " | ".join(t for t in bubble_texts if t)
    assert "what was decided?" in joined
    assert "Ship Friday." in joined


def test_send_emits_signal_with_text_and_recording_id(qapp):
    card = ChatCard(recording_id=42, history=[])
    captured: list[tuple[int, str]] = []
    card.send_requested.connect(lambda rid, txt: captured.append((rid, txt)))
    card._input.setPlainText("how long was the meeting?")
    card._send_btn.click()
    assert captured == [(42, "how long was the meeting?")]


def test_send_does_nothing_when_input_is_blank(qapp):
    card = ChatCard(recording_id=42, history=[])
    captured: list[tuple[int, str]] = []
    card.send_requested.connect(lambda rid, txt: captured.append((rid, txt)))
    card._input.setPlainText("   \n   ")
    card._send_btn.click()
    assert captured == []


def test_set_pending_disables_input_and_send(qapp):
    card = ChatCard(recording_id=10, history=[])
    card.set_pending(True)
    assert not card._input.isEnabled()
    assert not card._send_btn.isEnabled()
    card.set_pending(False)
    assert card._input.isEnabled()
    assert card._send_btn.isEnabled()


def test_disabled_card_shows_hint_and_blocks_send(qapp):
    card = ChatCard(
        recording_id=10, history=[], enabled=False,
        disabled_hint="Set your Anthropic API key in Settings → AI to chat.",
    )
    assert "Anthropic" in card._disabled_label.text()
    captured: list[tuple[int, str]] = []
    card.send_requested.connect(lambda rid, txt: captured.append((rid, txt)))
    card._input.setPlainText("hi")
    card._send_btn.click()
    assert captured == []


def test_append_assistant_message_adds_bubble(qapp):
    card = ChatCard(recording_id=10, history=[])
    card.append_assistant_message("here's an answer")
    texts = [b.toPlainText() for b in card._message_container.findChildren(QTextEdit)
             if b is not card._input]
    assert any("here's an answer" in t for t in texts)


def test_append_error_message_renders_distinctly(qapp):
    card = ChatCard(recording_id=10, history=[])
    card.append_error_message("Anthropic key invalid")
    # An error bubble should exist and contain the text; we don't pin a
    # specific style class, just confirm the content is visible.
    texts = [b.toPlainText() for b in card._message_container.findChildren(QTextEdit)
             if b is not card._input]
    assert any("Anthropic key invalid" in t for t in texts)
```

- [ ] **Step 2: Run to verify failure**

Run: `& "<uv>" run pytest tests/ui/test_chat_card.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `ui/chat_card.py`**

```python
"""Chat-with-Claude card for the SummaryPane.

Self-contained: renders the persisted conversation history, exposes a
single-line autoresizing input + Send button, and emits send_requested
when the user submits a message. The App owns the network call.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QColor

from teams_transcriber.storage.chat import ChatMessage


class _ChatInput(QTextEdit):
    """QTextEdit where Enter submits and Shift+Enter inserts a newline."""

    submit_requested = Signal()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            e.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submit_requested.emit()
            e.accept()
            return
        super().keyPressEvent(e)


class ChatCard(QFrame):
    """A meeting's chat-with-Claude card. Sized to embed in SummaryPane."""

    send_requested = Signal(int, str)   # recording_id, user_text

    def __init__(
        self,
        recording_id: int,
        history: Iterable[ChatMessage],
        *,
        enabled: bool = True,
        disabled_hint: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._recording_id = recording_id
        self._enabled = enabled

        self.setProperty("card", True)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(12); shadow.setColor(QColor(0, 0, 0, 14))
        shadow.setOffset(0, 1); self.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16); outer.setSpacing(8)

        header = QLabel("Chat about this meeting")
        header.setStyleSheet("font-size: 14px; font-weight: 600;")
        outer.addWidget(header)

        # Scrollable message list.
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(180); scroll.setMaximumHeight(360)
        self._message_container = QWidget()
        self._msg_layout = QVBoxLayout(self._message_container)
        self._msg_layout.setContentsMargins(0, 0, 0, 0); self._msg_layout.setSpacing(6)
        scroll.setWidget(self._message_container)
        outer.addWidget(scroll, 1)
        self._scroll = scroll

        # Empty-state placeholder.
        self._placeholder = QLabel("Ask Claude about this meeting…")
        self._placeholder.setProperty("role", "muted")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._msg_layout.addWidget(self._placeholder)

        # Render existing history.
        for msg in history:
            self._add_bubble(msg.role, msg.content)

        # Disabled hint (only shown when not enabled).
        self._disabled_label = QLabel(disabled_hint or "")
        self._disabled_label.setWordWrap(True)
        self._disabled_label.setStyleSheet("color: #B45309; font-size: 12px;")
        self._disabled_label.setVisible(not enabled and bool(disabled_hint))
        outer.addWidget(self._disabled_label)

        # Input row.
        row = QHBoxLayout(); row.setSpacing(8)
        self._input = _ChatInput()
        self._input.setPlaceholderText(
            "Ask a question… (Enter to send, Shift+Enter for newline)"
        )
        self._input.setMinimumHeight(36)
        self._input.setMaximumHeight(120)
        self._input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )
        self._input.setEnabled(enabled)
        self._input.submit_requested.connect(self._on_send_clicked)
        row.addWidget(self._input, 1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setProperty("role", "primary")
        self._send_btn.setFixedHeight(36)
        self._send_btn.setEnabled(enabled)
        self._send_btn.clicked.connect(self._on_send_clicked)
        row.addWidget(self._send_btn)
        outer.addLayout(row)

    # ---- public API ---------------------------------------------------

    def append_user_message(self, text: str) -> None:
        self._add_bubble("user", text)

    def append_assistant_message(self, text: str) -> None:
        self._add_bubble("assistant", text)

    def append_error_message(self, text: str) -> None:
        self._add_bubble("error", text)

    def set_pending(self, pending: bool) -> None:
        self._input.setEnabled(self._enabled and not pending)
        self._send_btn.setEnabled(self._enabled and not pending)
        self._send_btn.setText("Sending…" if pending else "Send")

    # ---- internals ----------------------------------------------------

    def _on_send_clicked(self) -> None:
        if not self._enabled:
            return
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._input.clear()
        self.send_requested.emit(self._recording_id, text)

    def _add_bubble(self, role: str, content: str) -> None:
        if self._placeholder is not None and self._placeholder.isVisible():
            self._placeholder.setVisible(False)
        bubble = QTextEdit()
        bubble.setReadOnly(True)
        bubble.setPlainText(content)
        bubble.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )
        bubble.setMaximumHeight(240)
        if role == "user":
            bubble.setStyleSheet(
                "background: #10B981; color: white; border-radius: 10px; padding: 8px;"
            )
        elif role == "error":
            bubble.setStyleSheet(
                "background: #FEE2E2; color: #991B1B; border-radius: 10px; "
                "padding: 8px; border: 1px solid #FCA5A5;"
            )
        else:
            bubble.setStyleSheet(
                "background: #FFFFFF; color: #111827; border-radius: 10px; "
                "padding: 8px; border: 1px solid #E5E7EB;"
            )
        self._msg_layout.addWidget(bubble)

        # Auto-scroll to the new message after the layout updates.
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
```

- [ ] **Step 4: Run to verify they pass**

Run: `& "<uv>" run pytest tests/ui/test_chat_card.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/chat_card.py tests/ui/test_chat_card.py
git commit -m "feat(ui): ChatCard widget (bubbles + Enter-to-send + pending state)"
```

---

## Task 4: SummaryPane integration

**Files:**
- Modify: `src/teams_transcriber/ui/summary_pane.py`
- Test: `tests/ui/test_summary_pane.py`

- [ ] **Step 1: Write failing tests**

Read `tests/ui/test_summary_pane.py` first to see its existing fixtures
(`db_with_summary` etc.). Append these tests:

```python
# tests/ui/test_summary_pane.py (append)

def test_chat_card_appears_when_transcript_segments_exist(qapp, db_with_summary):
    db, rid = db_with_summary
    # db_with_summary already inserts one transcript segment, so chat shows up.
    from teams_transcriber.ui.chat_card import ChatCard
    pane = SummaryPane(db, anthropic_key_getter=lambda: "k")
    pane.show_recording(rid)
    chat = pane.findChild(ChatCard)
    assert chat is not None


def test_chat_card_hidden_when_no_transcript(qapp, tmp_path):
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import (
        Recording, RecordingRepo, RecordingSource, RecordingStatus,
        Summary, SummaryRepo, build_database,
    )
    from teams_transcriber.ui.chat_card import ChatCard
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="t", one_line=None, summary="s",
        key_decisions=[], my_todos=[], action_items_others=[],
        follow_ups=[], topics=[], generated_at="x", model_used="m",
    ))
    pane = SummaryPane(db, anthropic_key_getter=lambda: "k")
    pane.show_recording(rec.id)
    assert pane.findChild(ChatCard) is None
    db.close()


def test_chat_card_disabled_when_no_api_key(qapp, db_with_summary):
    db, rid = db_with_summary
    from teams_transcriber.ui.chat_card import ChatCard
    pane = SummaryPane(db, anthropic_key_getter=lambda: "")
    pane.show_recording(rid)
    chat = pane.findChild(ChatCard)
    assert chat is not None and chat._enabled is False
    assert "Anthropic" in chat._disabled_label.text() or "API key" in chat._disabled_label.text()


def test_chat_send_signal_is_re_emitted_from_pane(qapp, db_with_summary):
    db, rid = db_with_summary
    pane = SummaryPane(db, anthropic_key_getter=lambda: "k")
    pane.show_recording(rid)
    from teams_transcriber.ui.chat_card import ChatCard
    chat = pane.findChild(ChatCard)
    assert chat is not None
    captured: list[tuple[int, str]] = []
    pane.chat_send_requested.connect(lambda r, t: captured.append((r, t)))
    chat._input.setPlainText("question?")
    chat._send_btn.click()
    assert captured == [(rid, "question?")]
```

- [ ] **Step 2: Run to verify failures**

Run: `& "<uv>" run pytest tests/ui/test_summary_pane.py -k "chat_" -v`
Expected: FAIL — `chat_send_requested` signal / `anthropic_key_getter`
constructor kwarg / chat card insertion all missing.

- [ ] **Step 3: Modify `summary_pane.py`**

Find the signals block and add:
```python
    chat_send_requested = Signal(int, str)    # recording_id, user_text
```

Find the `__init__` signature (currently has `wrike_available`); add the
new kwarg:
```python
    def __init__(
        self,
        db: Database,
        *,
        wrike_available: Callable[[], bool] | None = None,
        anthropic_key_getter: Callable[[], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._wrike_available = wrike_available
        self._anthropic_key_getter = anthropic_key_getter
        ...
```

Find the place in `show_recording` AFTER the existing button row wrapper is
added and BEFORE `self._layout.addStretch(1)`. INSERT the chat card:

```python
        # Chat-with-Claude card — only meaningful when we have transcript
        # segments to give Claude as context.
        from teams_transcriber.storage import TranscriptRepo
        from teams_transcriber.storage.chat import ChatRepo
        from teams_transcriber.ui.chat_card import ChatCard
        segments = TranscriptRepo(self._db).list_for_recording(recording_id)
        if segments:
            history = ChatRepo(self._db).list_for_recording(recording_id)
            api_key = (self._anthropic_key_getter or (lambda: ""))()
            if api_key:
                self._chat_card = ChatCard(
                    recording_id, history, enabled=True,
                )
            else:
                self._chat_card = ChatCard(
                    recording_id, history, enabled=False,
                    disabled_hint=(
                        "Set your Anthropic API key in Settings → AI to chat."
                    ),
                )
            self._chat_card.send_requested.connect(self.chat_send_requested.emit)
            self._layout.addWidget(self._chat_card)
        else:
            self._chat_card = None
```

(`self._chat_card` lets the App fetch the card to call
`append_assistant_message` / `set_pending` after the worker returns.)

- [ ] **Step 4: Run to verify they pass + full suite**

Run: `& "<uv>" run pytest tests/ui/test_summary_pane.py -v`
Expected: PASS.
Run: `& "<uv>" run pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/summary_pane.py tests/ui/test_summary_pane.py
git commit -m "feat(ui): embed ChatCard in SummaryPane when transcript exists"
```

---

## Task 5: App handler + threading

**Files:**
- Modify: `src/teams_transcriber/ui/app.py`
- Test: `tests/ui/test_app_chat_wiring.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/ui/test_app_chat_wiring.py — predicate-level (no full App build)

def test_chat_should_send_predicate():
    """Centralised check the App uses before dispatching a chat send."""
    from teams_transcriber.ui.app import _chat_should_send
    assert _chat_should_send(api_key="k", text="hi") is True
    assert _chat_should_send(api_key="",  text="hi") is False
    assert _chat_should_send(api_key="k", text="") is False
    assert _chat_should_send(api_key="k", text="   ") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `& "<uv>" run pytest tests/ui/test_app_chat_wiring.py -v`
Expected: FAIL — predicate undefined.

- [ ] **Step 3: Implement the App wiring**

In `src/teams_transcriber/ui/app.py`:

Add a module-level helper near the other small predicates (e.g. next to
`_wrike_should_offer_sync`):
```python
def _chat_should_send(*, api_key: str, text: str) -> bool:
    return bool(api_key) and bool(text.strip())
```

In `App.__init__`, where `SummaryPane` is constructed, add the
`anthropic_key_getter` kwarg:
```python
        self.summary = SummaryPane(
            self.db,
            wrike_available=self._wrike_is_configured,
            anthropic_key_getter=self._anthropic_key,
        )
```
After the existing `self.summary.wrike_sync_requested.connect(...)` line,
add:
```python
        self.summary.chat_send_requested.connect(self._on_chat_send)
```

Add the helper + handlers (place near `_wrike_is_configured`):
```python
    def _anthropic_key(self) -> str:
        import keyring
        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_ANTHROPIC
        try:
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER_ANTHROPIC) or ""
        except Exception:
            return ""

    def _on_chat_send(self, recording_id: int, text: str) -> None:
        import threading
        api_key = self._anthropic_key()
        if not _chat_should_send(api_key=api_key, text=text):
            return
        card = getattr(self.summary, "_chat_card", None)
        if card is None:
            return
        card.append_user_message(text)
        card.set_pending(True)
        threading.Thread(
            target=self._chat_worker,
            args=(recording_id, text, api_key),
            daemon=True,
        ).start()

    def _chat_worker(self, recording_id: int, text: str, api_key: str) -> None:
        from PySide6.QtCore import QTimer
        from teams_transcriber.chat import (
            ChatApiError, ChatAuthError, ChatTokenLimitError, ask,
        )
        try:
            reply = ask(
                self.db, recording_id, text,
                api_key=api_key, model=self.settings.ai_model,
            )
        except ChatAuthError as exc:
            err = "Anthropic key invalid — reset in Settings → AI."
            QTimer.singleShot(0, self.window,
                              lambda: self._on_chat_failed(recording_id, err))
            return
        except ChatTokenLimitError as exc:
            err = str(exc)
            QTimer.singleShot(0, self.window,
                              lambda: self._on_chat_failed(recording_id, err))
            return
        except ChatApiError as exc:
            err = f"Chat failed: {exc}"
            QTimer.singleShot(0, self.window,
                              lambda: self._on_chat_failed(recording_id, err))
            return
        QTimer.singleShot(0, self.window,
                          lambda: self._on_chat_done(recording_id, reply))

    def _on_chat_done(self, recording_id: int, reply: str) -> None:
        # Only update the UI if the user is still on this recording — the
        # message is already persisted in the DB either way.
        if self.summary._current_recording_id != recording_id:
            return
        card = getattr(self.summary, "_chat_card", None)
        if card is None:
            return
        card.set_pending(False)
        card.append_assistant_message(reply)

    def _on_chat_failed(self, recording_id: int, err: str) -> None:
        if self.summary._current_recording_id != recording_id:
            return
        card = getattr(self.summary, "_chat_card", None)
        if card is None:
            return
        card.set_pending(False)
        card.append_error_message(err)
```

- [ ] **Step 4: Run + import smoke + full suite**

Run: `& "<uv>" run pytest tests/ui/test_app_chat_wiring.py -v`
Expected: PASS.
Run: `& "<uv>" run python -c "import teams_transcriber.ui.app; print('OK')"`
Expected: OK.
Run: `& "<uv>" run pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py tests/ui/test_app_chat_wiring.py
git commit -m "feat(app): chat send → worker thread → main-thread hop, persists turns"
```

---

## Final verification

- [ ] **Full suite green**: `& "<uv>" run pytest -q` — all passing.
- [ ] **Manual smoke**:
  - Settings → AI → confirm a valid Anthropic key is set.
  - Open a meeting with a transcript and an existing summary → the chat
    card appears below the button row. Ask a question that requires the
    transcript ("what time did the meeting start?" / "what did Jennifer
    actually say about the floor plan?") → Claude answers in a white
    bubble.
  - Toggle a my-todo while the chat reply is in flight → no crash; the
    reply arrives.
  - Open a different meeting mid-flight → reply lands on the original
    meeting's history (you'll see it when you revisit), no leakage.
  - Settings → AI → clear the key, reopen the meeting → input is greyed
    with the "Set your Anthropic API key" hint.
  - Delete a meeting → its chat history is gone via CASCADE.

- [ ] **Update memory** in `memory/project_teams_transcriber.md` with a
  Phase 12 summary, then invoke `superpowers:finishing-a-development-branch`.

---

## Self-review notes (author)

- **Spec coverage:**
  - schema + repo → Task 1.
  - orchestrator with cached system block + history + error mapping →
    Task 2.
  - card widget (bubbles, send, pending, disabled) → Task 3.
  - SummaryPane integration (transcript-gated + API-key getter +
    re-emitted signal) → Task 4.
  - App handler + threading (worker + main-thread hop + per-recording
    safety) → Task 5.
- **Placeholder scan:** none. Tests show actual code; impl shows actual
  code.
- **Type/name consistency:** `ChatMessage` / `ChatRepo` /
  `chat_send_requested(int, str)` / `_chat_should_send(*, api_key, text)`
  / `ChatCard.set_pending(bool)` / `append_assistant_message(str)` /
  `append_error_message(str)` used uniformly across tasks. `settings.ai_model`
  is the existing summarizer config key — verified at plan-write time via
  `summarizer.py:211`.
- **DB API:** all SQL via `with self._db.connect() as conn:` (the lesson
  from Phase 11's `WrikeSyncRepo` adjustment).
- **Threading:** every cross-thread Qt dispatch uses
  `QTimer.singleShot(0, self.window, callable)` (the lesson from Phase 11
  v0.7.3).
