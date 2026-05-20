from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from teams_transcriber.config import Settings
from teams_transcriber.events import EventBus, SummaryReady
from teams_transcriber.storage import (
    Channel,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    SummaryRepo,
    TranscriptRepo,
    TranscriptSegment,
    build_database,
)
from teams_transcriber.summarizer import SUMMARY_TOOL_NAME, Summarizer

# --- Anthropic SDK fakes -------------------------------------------------

@dataclass
class _FakeToolUseBlock:
    type: str
    name: str
    input: dict[str, Any]


@dataclass
class _FakeResponse:
    content: list[_FakeToolUseBlock]
    stop_reason: str = "tool_use"


class FakeAnthropic:
    """Returns canned tool-use responses. Tracks calls for assertion."""

    def __init__(self, scripted: list[Any]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    class _Messages:
        def __init__(self, parent: FakeAnthropic) -> None:
            self._parent = parent

        def create(self, **kwargs: Any) -> Any:
            self._parent.calls.append(kwargs)
            response = self._parent._scripted.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    @property
    def messages(self) -> _Messages:
        return self._Messages(self)


def _canned_ok(title: str = "Q2 sync") -> _FakeResponse:
    payload = {
        "title": title,
        "one_line": "Aligned on billing rewrite for July.",
        "summary": "We discussed the billing rewrite.",
        "key_decisions": ["Ship in July"],
        "my_todos": [{"task": "Write API stub", "context": None, "due": None}],
        "action_items_others": [{"who": "Sarah", "task": "Migration doc", "due": None}],
        "follow_ups": ["Revisit pricing"],
        "topics": ["billing"],
    }
    return _FakeResponse(content=[
        _FakeToolUseBlock(type="tool_use", name=SUMMARY_TOOL_NAME, input=payload),
    ])


@pytest.fixture
def setup_recording(tmp_path):
    from teams_transcriber.paths import AppPaths
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-14T10:00:00+00:00",
        ended_at="2026-05-14T10:05:00+00:00",
        source=RecordingSource.TEAMS,
        detected_title="X", display_title=None,
        audio_path=None, audio_deleted_at=None,
        duration_ms=300_000, status=RecordingStatus.SUMMARIZING,
        error_message=None,
    ))
    assert rec.id is not None
    TranscriptRepo(db).append_many([
        TranscriptSegment(None, rec.id, 0, 2000, Channel.OTHERS, "Welcome everyone"),
        TranscriptSegment(None, rec.id, 2000, 4500, Channel.ME, "Hi I'll own the stub"),
    ])
    yield db, rec.id
    db.close()


def test_summarize_writes_summary_and_sets_title(setup_recording) -> None:
    db, rec_id = setup_recording
    bus = EventBus()
    received: list[SummaryReady] = []
    bus.subscribe(SummaryReady, received.append)
    settings = Settings()

    client = FakeAnthropic(scripted=[_canned_ok(title="Q2 roadmap sync")])
    s = Summarizer(bus=bus, db=db, settings=settings, client_factory=lambda _key: client)
    s.summarize(rec_id, api_key="sk-test")

    assert len(received) == 1
    assert received[0].recording_id == rec_id

    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.display_title == "Q2 roadmap sync"
    assert rec.status == RecordingStatus.DONE

    summary = SummaryRepo(db).get(rec_id)
    assert summary is not None
    assert summary.title == "Q2 roadmap sync"
    assert summary.my_todos[0].task == "Write API stub"


def test_summarize_retries_on_transient_error(setup_recording) -> None:
    db, rec_id = setup_recording
    bus = EventBus()
    settings = Settings()

    scripted = [RuntimeError("503 service unavailable"), _canned_ok()]
    client = FakeAnthropic(scripted=scripted)
    s = Summarizer(
        bus=bus, db=db, settings=settings,
        client_factory=lambda _key: client,
        sleep=lambda _s: None,
    )
    s.summarize(rec_id, api_key="sk-test")

    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.DONE
    # Made two calls: one failed, one succeeded.
    assert len(client.calls) == 2


def test_summarize_marks_failed_after_max_retries(setup_recording) -> None:
    db, rec_id = setup_recording
    bus = EventBus()
    settings = Settings()

    client = FakeAnthropic(scripted=[RuntimeError("boom")] * 5)
    s = Summarizer(
        bus=bus, db=db, settings=settings,
        client_factory=lambda _key: client,
        sleep=lambda _s: None,
    )
    s.summarize(rec_id, api_key="sk-test")

    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.SUMMARY_FAILED
    assert "boom" in (rec.error_message or "")


def test_summarize_skips_when_no_api_key(setup_recording) -> None:
    db, rec_id = setup_recording
    bus = EventBus()
    client = FakeAnthropic(scripted=[_canned_ok()])
    s = Summarizer(
        bus=bus, db=db, settings=Settings(),
        client_factory=lambda _key: client,
    )
    s.summarize(rec_id, api_key=None)

    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.SUMMARY_FAILED
    assert "api key" in (rec.error_message or "").lower()


def test_summarize_marks_failed_for_oversize_transcript(setup_recording, monkeypatch) -> None:
    """Transcripts beyond the char-limit guard must fail-loud, not silently
    overflow Claude's context window."""
    from teams_transcriber import summarizer as summarizer_module

    db, rec_id = setup_recording
    monkeypatch.setattr(summarizer_module, "_TRANSCRIPT_CHAR_LIMIT", 50)
    client = FakeAnthropic(scripted=[])  # should never be called
    s = Summarizer(
        bus=EventBus(), db=db, settings=Settings(),
        client_factory=lambda _k: client,
    )
    s.summarize(rec_id, api_key="sk-test")

    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.SUMMARY_FAILED
    assert "too long" in (rec.error_message or "").lower()
    assert len(client.calls) == 0


def test_summarize_sends_cache_control_on_system_and_tools(setup_recording) -> None:
    """Prompt caching reduces cost; ensure cache_control is applied."""
    db, rec_id = setup_recording
    client = FakeAnthropic(scripted=[_canned_ok()])
    s = Summarizer(
        bus=EventBus(), db=db, settings=Settings(),
        client_factory=lambda _k: client,
    )
    s.summarize(rec_id, api_key="sk-test")

    assert len(client.calls) == 1
    call = client.calls[0]
    # System is a content-blocks list with cache_control on the prompt block.
    assert isinstance(call["system"], list)
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Tool definition carries cache_control too.
    assert call["tools"][0]["cache_control"] == {"type": "ephemeral"}


def test_summarize_marks_failed_when_tool_input_is_malformed(setup_recording) -> None:
    db, rec_id = setup_recording
    bad_block = _FakeToolUseBlock(
        type="tool_use", name=SUMMARY_TOOL_NAME,
        input={"title": "x"},  # missing required fields
    )
    bad_response = _FakeResponse(content=[bad_block])
    client = FakeAnthropic(scripted=[bad_response, bad_response, bad_response])
    s = Summarizer(
        bus=EventBus(), db=db, settings=Settings(),
        client_factory=lambda _k: client,
        sleep=lambda _s: None,
    )
    s.summarize(rec_id, api_key="sk-test")
    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.SUMMARY_FAILED


def test_summarizer_publishes_summary_failed_on_empty_transcript(tmp_path) -> None:
    """When the transcript is empty, summarize() publishes SummaryFailed."""
    from teams_transcriber.config import load_settings
    from teams_transcriber.events import EventBus, SummaryFailed
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        build_database,
    )
    from teams_transcriber.summarizer import Summarizer

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    settings = load_settings(paths)
    bus = EventBus()
    received: list[SummaryFailed] = []
    bus.subscribe(SummaryFailed, received.append)

    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-20T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t", audio_path=None,
        audio_deleted_at=None, duration_ms=None,
        status=RecordingStatus.SUMMARIZING, error_message=None,
    ))
    summ = Summarizer(bus=bus, db=db, settings=settings)
    summ.summarize(rec.id, api_key="sk-fake-not-used-empty-transcript-short-circuits-first")
    db.close()

    assert len(received) == 1
    assert "empty" in received[0].error_message.lower()
    assert received[0].recording_id == rec.id


def test_summarizer_publishes_summary_failed_on_missing_api_key(tmp_path) -> None:
    """When api_key is None, summarize() publishes SummaryFailed."""
    from teams_transcriber.config import load_settings
    from teams_transcriber.events import EventBus, SummaryFailed
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        build_database,
    )
    from teams_transcriber.summarizer import Summarizer

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    settings = load_settings(paths)
    bus = EventBus()
    received: list[SummaryFailed] = []
    bus.subscribe(SummaryFailed, received.append)

    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-20T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t", audio_path=None,
        audio_deleted_at=None, duration_ms=None,
        status=RecordingStatus.SUMMARIZING, error_message=None,
    ))
    summ = Summarizer(bus=bus, db=db, settings=settings)
    summ.summarize(rec.id, api_key=None)
    db.close()

    assert len(received) == 1
    assert "api key" in received[0].error_message.lower()
    assert received[0].recording_id == rec.id
