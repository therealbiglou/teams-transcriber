"""Tests for LiveTranscriber."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from teams_transcriber.config import load_settings
from teams_transcriber.events import (
    EventBus,
    LiveSegmentAvailable,
    LiveTranscriptionDegraded,
)
from teams_transcriber.live_transcriber import LiveTranscriber
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import Channel, build_database


@dataclass(slots=True)
class _StubSegment:
    start: float
    end: float
    text: str


class _StubModel:
    """Records each `transcribe(...)` call and returns scripted segments.

    Per-call FIFO queue: each `queue(segs)` enqueues a batch; each
    `transcribe(...)` call pops the next batch. This decouples test setup
    from worker timing — the test queues all its scripted batches upfront,
    and the worker consumes them in the order they were queued.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, np.ndarray]] = []
        self._queued_batches: list[list[_StubSegment]] = []

    def queue(self, segments: list[_StubSegment]) -> None:
        self._queued_batches.append(list(segments))

    def transcribe(
        self,
        audio: Any,
        language: str | None = None,
        vad_filter: bool = True,
    ) -> tuple[Any, dict[str, Any]]:
        self.calls.append(("transcribe", np.asarray(audio).copy()))
        if self._queued_batches:
            out = self._queued_batches.pop(0)
        else:
            out = []
        return iter(out), {}


@pytest.fixture
def fresh_db(tmp_path):
    db_path = tmp_path / "test.db"
    db = build_database(db_path)
    db.initialize()
    yield db
    db.close()


@pytest.fixture
def app_paths(tmp_path):
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    return paths


def _make_recording(db) -> int:
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
    )
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-18T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t", audio_path=None,
        audio_deleted_at=None, duration_ms=None,
        status=RecordingStatus.RECORDING, error_message=None,
    ))
    assert rec.id is not None
    return rec.id


def test_live_transcriber_emits_segments_per_channel(fresh_db, app_paths) -> None:
    bus = EventBus()
    settings = load_settings(app_paths)
    events: list[LiveSegmentAvailable] = []
    bus.subscribe(LiveSegmentAvailable, events.append)

    model = _StubModel()
    rec_id = _make_recording(fresh_db)
    lt = LiveTranscriber(
        bus=bus, db=fresh_db, settings=settings,
        model_factory=lambda *_a, **_kw: model,
        flush_interval_ms=200, max_wait_ms=400,
    )
    lt.start(rec_id)

    # 0.3 s of audio per channel — both will trip the flush threshold.
    pcm = np.zeros(int(0.3 * 16_000), dtype=np.float32)
    model.queue([_StubSegment(0.0, 0.3, "hi from mic")])
    lt.feed(Channel.ME, pcm)
    model.queue([_StubSegment(0.0, 0.3, "hi from loop")])
    lt.feed(Channel.OTHERS, pcm)

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and len(events) < 2:
        time.sleep(0.02)

    lt.flush_and_stop()

    assert len(events) == 2
    channels = sorted(e.segment.channel for e in events)
    assert channels == [Channel.ME, Channel.OTHERS]


def test_live_transcriber_alternates_channels(fresh_db, app_paths) -> None:
    """Even under heavy single-channel load, processing alternates."""
    bus = EventBus()
    settings = load_settings(app_paths)
    events: list[LiveSegmentAvailable] = []
    bus.subscribe(LiveSegmentAvailable, events.append)

    model = _StubModel()
    rec_id = _make_recording(fresh_db)
    lt = LiveTranscriber(
        bus=bus, db=fresh_db, settings=settings,
        model_factory=lambda *_a, **_kw: model,
        flush_interval_ms=100, max_wait_ms=200,
    )
    lt.start(rec_id)

    pcm = np.zeros(int(0.12 * 16_000), dtype=np.float32)
    for i in range(5):
        model.queue([_StubSegment(i * 0.12, (i + 1) * 0.12, f"mic-{i}")])
        lt.feed(Channel.ME, pcm)
        model.queue([_StubSegment(i * 0.12, (i + 1) * 0.12, f"loop-{i}")])
        lt.feed(Channel.OTHERS, pcm)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and len(events) < 10:
        time.sleep(0.02)
    lt.flush_and_stop()

    channels = [e.segment.channel for e in events]
    for prev, curr in zip(channels, channels[1:]):
        assert prev != curr, f"saw two consecutive {prev} segments: {channels}"


def test_live_transcriber_persists_segments(fresh_db, app_paths) -> None:
    """Segments are written to TranscriptRepo as they're produced."""
    from teams_transcriber.storage import TranscriptRepo

    bus = EventBus()
    settings = load_settings(app_paths)
    model = _StubModel()
    rec_id = _make_recording(fresh_db)
    lt = LiveTranscriber(
        bus=bus, db=fresh_db, settings=settings,
        model_factory=lambda *_a, **_kw: model,
        flush_interval_ms=100, max_wait_ms=200,
    )
    lt.start(rec_id)
    pcm = np.zeros(int(0.12 * 16_000), dtype=np.float32)
    model.queue([_StubSegment(0.0, 0.12, "persisted-mic")])
    lt.feed(Channel.ME, pcm)
    time.sleep(0.6)
    lt.flush_and_stop()

    rows = TranscriptRepo(fresh_db).list_for_recording(rec_id)
    assert any(r.text == "persisted-mic" for r in rows)


def test_live_transcriber_publishes_degraded_on_model_error(fresh_db, app_paths) -> None:
    bus = EventBus()
    settings = load_settings(app_paths)
    degraded: list[LiveTranscriptionDegraded] = []
    bus.subscribe(LiveTranscriptionDegraded, degraded.append)

    class BadModel:
        def transcribe(self, *_a, **_kw):
            raise RuntimeError("cuda oom (simulated)")

    rec_id = _make_recording(fresh_db)
    lt = LiveTranscriber(
        bus=bus, db=fresh_db, settings=settings,
        model_factory=lambda *_a, **_kw: BadModel(),
        flush_interval_ms=100, max_wait_ms=200,
    )
    lt.start(rec_id)
    pcm = np.zeros(int(0.12 * 16_000), dtype=np.float32)
    lt.feed(Channel.ME, pcm)
    time.sleep(0.6)
    lt.flush_and_stop()

    assert len(degraded) >= 1
    assert degraded[0].recording_id == rec_id
