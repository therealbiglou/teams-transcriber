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
    repo = RecordingRepo(db)
    rec = repo.create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="Potter Sync",
        display_title="Potter Sync", audio_path=None, audio_deleted_at=None,
        duration_ms=1500, status=RecordingStatus.DONE,
        error_message=None, manual_notes=None,
    ))
    repo.set_manual_notes(rec.id, "<p>important: floor plan is rev 3</p>")
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
    """No summary + non-empty transcript: system block still works."""
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


def test_strip_html_decodes_entities():
    from teams_transcriber.chat import _strip_html
    assert _strip_html("<p>fish &amp; chips</p>") == "fish & chips"
    assert _strip_html("a &lt; b") == "a < b"
