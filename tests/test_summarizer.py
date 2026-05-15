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
