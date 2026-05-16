from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from teams_transcriber.audio.source import FakeAudioSource
from teams_transcriber.config import Settings
from teams_transcriber.events import EventBus, MeetingDetected, MeetingEnded, SummaryReady
from teams_transcriber.paths import AppPaths
from teams_transcriber.pipeline import Pipeline
from teams_transcriber.storage import (
    RecordingRepo,
    RecordingStatus,
    SummaryRepo,
    TranscriptRepo,
    build_database,
)
from teams_transcriber.summarizer import SUMMARY_TOOL_NAME, Summarizer
from teams_transcriber.transcriber import Transcriber


def _make_source(seconds: float) -> FakeAudioSource:
    n = int(seconds * 16_000)
    t = np.linspace(0, seconds, n, endpoint=False, dtype=np.float32)
    mic = 0.25 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    loop = 0.25 * np.sin(2 * np.pi * 880 * t).astype(np.float32)
    return FakeAudioSource(mic_samples=mic, loopback_samples=loop)


class _BoomSource:
    def read_chunk(self, num_frames: int):
        del num_frames
        raise RuntimeError("audio device gone")

    def close(self) -> None:
        pass


@dataclass
class _FakeSeg:
    start: float
    end: float
    text: str


class _FakeWhisper:
    def __init__(self, *_a, **_kw):
        pass
    def transcribe(self, *_a, **_kw):
        return iter([_FakeSeg(0.0, 0.5, "Hello from pipeline test")]), object()


@dataclass
class _TB:
    type: str
    name: str
    input: dict


@dataclass
class _R:
    content: list


class _FakeClient:
    class _M:
        def create(self, **_kw):
            return _R(content=[_TB(
                "tool_use", SUMMARY_TOOL_NAME,
                {
                    "title": "Pipeline test", "one_line": "ok", "summary": "ok",
                    "key_decisions": [], "my_todos": [],
                    "action_items_others": [], "follow_ups": [], "topics": [],
                },
            )])
    @property
    def messages(self):
        return self._M()


def test_end_to_end_with_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    bus = EventBus()
    settings = Settings()

    source_holder = [_make_source(seconds=0.5)]
    transcriber = Transcriber(
        bus=bus, db=db, settings=settings, model_factory=lambda *_a, **_kw: _FakeWhisper(),
    )
    summarizer = Summarizer(
        bus=bus, db=db, settings=settings,
        client_factory=lambda _k: _FakeClient(),
    )

    pipeline = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: source_holder.pop(0),
        transcriber=transcriber,
        summarizer=summarizer,
    )

    summaries_ready: list[SummaryReady] = []
    bus.subscribe(SummaryReady, summaries_ready.append)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    bus.publish(MeetingDetected(window_title="Pipeline test | Microsoft Teams"))
    # Wait for the recorder thread to drain the (very small) fake source.
    time.sleep(0.5)
    bus.publish(MeetingEnded())

    # Drain the executor so post-processing completes.
    pipeline.shutdown()

    assert len(summaries_ready) == 1

    recs = RecordingRepo(db).list_recent()
    assert len(recs) == 1
    assert recs[0].status == RecordingStatus.DONE
    summary = SummaryRepo(db).get(recs[0].id)
    assert summary is not None
    assert summary.title == "Pipeline test"

    segments = TranscriptRepo(db).list_for_recording(recs[0].id)
    assert any("Hello from pipeline test" in s.text for s in segments)

    db.close()

    # Reference pipeline so it isn't garbage collected mid-run.
    del pipeline


def test_pipeline_releases_recorder_on_failure(tmp_path: Path) -> None:
    from teams_transcriber.events import RecordingFailed

    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    bus = EventBus()
    settings = Settings()

    failed: list[RecordingFailed] = []
    bus.subscribe(RecordingFailed, failed.append)

    pipeline = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: _BoomSource(),  # type: ignore[arg-type,return-value]
    )
    del pipeline  # not used directly; subscriptions wire it in

    # Trigger the meeting flow. The recorder's _run will explode immediately.
    bus.publish(MeetingDetected(window_title="X"))

    import time
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not failed:
        time.sleep(0.01)

    assert len(failed) == 1

    # After the failure, a second MeetingDetected creates a second recording row
    # (would not happen if _recorder were still set).
    bus.publish(MeetingDetected(window_title="Y"))

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and len(failed) < 2:
        time.sleep(0.01)

    rec_repo = RecordingRepo(db)
    recs = rec_repo.list_recent()
    assert len(recs) == 2  # both attempts created rows; both failed

    db.close()


def test_pipeline_recovers_stuck_recordings_on_init(tmp_path: Path) -> None:
    """Recordings left in TRANSCRIBING/SUMMARIZING at startup must be marked
    failed so the UI can offer retry."""
    from teams_transcriber.storage import Recording, RecordingRepo, RecordingSource

    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    repo = RecordingRepo(db)

    stuck_t = repo.create(Recording(
        id=None, started_at="2026-05-15T10:00:00+00:00",
        ended_at="2026-05-15T10:05:00+00:00",
        source=RecordingSource.TEAMS,
        detected_title="stuck-transcribing", display_title=None,
        audio_path=None, audio_deleted_at=None,
        duration_ms=300_000, status=RecordingStatus.TRANSCRIBING,
        error_message=None,
    ))
    stuck_s = repo.create(Recording(
        id=None, started_at="2026-05-15T11:00:00+00:00",
        ended_at="2026-05-15T11:05:00+00:00",
        source=RecordingSource.TEAMS,
        detected_title="stuck-summarizing", display_title=None,
        audio_path=None, audio_deleted_at=None,
        duration_ms=300_000, status=RecordingStatus.SUMMARIZING,
        error_message=None,
    ))
    healthy = repo.create(Recording(
        id=None, started_at="2026-05-15T12:00:00+00:00",
        ended_at="2026-05-15T12:05:00+00:00",
        source=RecordingSource.TEAMS,
        detected_title="already-done", display_title=None,
        audio_path=None, audio_deleted_at=None,
        duration_ms=300_000, status=RecordingStatus.DONE,
        error_message=None,
    ))

    Pipeline(
        bus=EventBus(), db=db, paths=paths, settings=Settings(),
        audio_source_factory=lambda: _make_source(0.1),
    )

    assert repo.get(stuck_t.id).status == RecordingStatus.TRANSCRIPTION_FAILED
    assert "interrupted" in (repo.get(stuck_t.id).error_message or "").lower()
    assert repo.get(stuck_s.id).status == RecordingStatus.SUMMARY_FAILED
    assert "interrupted" in (repo.get(stuck_s.id).error_message or "").lower()
    assert repo.get(healthy.id).status == RecordingStatus.DONE

    db.close()


def test_pipeline_retry_summary_dispatches_to_summarizer(tmp_path: Path) -> None:
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()

    calls: list[tuple[int, str | None]] = []

    class _SummSpy:
        def summarize(self, recording_id: int, *, api_key: str | None) -> None:
            calls.append((recording_id, api_key))

    pipeline = Pipeline(
        bus=EventBus(), db=db, paths=paths, settings=Settings(),
        audio_source_factory=lambda: _make_source(0.1),
        summarizer=_SummSpy(),  # type: ignore[arg-type]
    )
    pipeline.retry_summary(42, api_key="sk-test")
    assert calls == [(42, "sk-test")]
    db.close()


def test_pipeline_runs_post_processing_on_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The transcribe+summarize chain must run off the publishing thread."""
    import threading

    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    bus = EventBus()
    settings = Settings()

    publisher_thread_id = threading.get_ident()
    transcribe_thread_ids: list[int] = []

    class _ThreadSpyTranscriber:
        def transcribe(self, recording_id: int) -> None:
            transcribe_thread_ids.append(threading.get_ident())

    sources = [_make_source(seconds=0.5)]
    pipeline = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: sources.pop(0),
        transcriber=_ThreadSpyTranscriber(),  # type: ignore[arg-type]
        summarizer=Summarizer(
            bus=bus, db=db, settings=settings,
            client_factory=lambda _k: _FakeClient(),
        ),
    )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    bus.publish(MeetingDetected(window_title="X | Microsoft Teams"))
    import time
    time.sleep(0.5)
    bus.publish(MeetingEnded())

    pipeline.shutdown()  # drain the executor

    assert len(transcribe_thread_ids) == 1
    assert transcribe_thread_ids[0] != publisher_thread_id

    db.close()
