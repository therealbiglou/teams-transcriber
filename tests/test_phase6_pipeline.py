"""Phase 6 pipeline integration tests."""

from __future__ import annotations

import numpy as np
import pytest

from teams_transcriber.audio.source import FakeAudioSource, NoAudioDevicesError
from teams_transcriber.config import load_settings
from teams_transcriber.events import EventBus, RecordingDeviceFallback, RecordingFailed
from teams_transcriber.paths import AppPaths
from teams_transcriber.pipeline import Pipeline
from teams_transcriber.storage import build_database


def _make_env(tmp_path):
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    settings = load_settings(paths)
    return paths, db, settings


class _NoopTranscriber:
    def transcribe(self, rid: int) -> None: pass


class _NoopSummarizer:
    def summarize(self, rid: int, *, api_key) -> None: pass


def test_pipeline_skips_live_transcriber_when_disabled(tmp_path, monkeypatch) -> None:
    """When transcription.live_enabled is False, no LiveTranscriber is created."""
    paths, db, settings = _make_env(tmp_path)
    bus = EventBus()
    mic = np.zeros(48_000, dtype=np.float32)
    loop = np.zeros(48_000, dtype=np.float32)
    source = FakeAudioSource(mic, loop)

    instantiated: list[str] = []

    class _SpyLive:
        def __init__(self, *_a, **_kw):
            instantiated.append("created")
        def start(self, *_a, **_kw): pass
        def feed(self, *_a, **_kw): pass
        def flush_and_stop(self): pass

    monkeypatch.setattr("teams_transcriber.pipeline.LiveTranscriber", _SpyLive)
    assert settings.transcription_live_enabled is False

    p = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: source,
        meeting_watcher=None,
        transcriber=_NoopTranscriber(),
        summarizer=_NoopSummarizer(),
    )
    p.start_manual(detected_title="t")
    source.run_until_exhausted()
    p.stop_manual()
    p.shutdown()
    db.close()

    assert instantiated == []


def test_pipeline_creates_live_transcriber_when_enabled(tmp_path, monkeypatch) -> None:
    """When transcription.live_enabled is True, the LiveTranscriber IS created."""
    paths, db, settings = _make_env(tmp_path)
    settings._raw["transcription"]["live_enabled"] = True
    bus = EventBus()
    mic = np.zeros(48_000, dtype=np.float32)
    loop = np.zeros(48_000, dtype=np.float32)
    source = FakeAudioSource(mic, loop)

    instantiated: list[str] = []

    class _SpyLive:
        def __init__(self, *_a, **_kw):
            instantiated.append("created")
        def start(self, *_a, **_kw): pass
        def feed(self, *_a, **_kw): pass
        def flush_and_stop(self): pass

    monkeypatch.setattr("teams_transcriber.pipeline.LiveTranscriber", _SpyLive)

    p = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: source,
        meeting_watcher=None,
        transcriber=_NoopTranscriber(),
        summarizer=_NoopSummarizer(),
    )
    p.start_manual(detected_title="t")
    source.run_until_exhausted()
    p.stop_manual()
    p.shutdown()
    db.close()

    assert instantiated == ["created"]


def test_pipeline_handles_no_audio_devices(tmp_path) -> None:
    """When the source factory raises NoAudioDevicesError, publish RecordingFailed."""
    paths, db, settings = _make_env(tmp_path)
    bus = EventBus()
    failed: list[RecordingFailed] = []
    bus.subscribe(RecordingFailed, failed.append)

    def explode():
        raise NoAudioDevicesError("No audio devices available — check Settings → Audio.")

    p = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=explode,
        meeting_watcher=None,
        transcriber=_NoopTranscriber(),
        summarizer=_NoopSummarizer(),
    )
    rid = p.start_manual(detected_title="t")
    assert rid == -1
    assert len(failed) == 1
    assert "audio devices" in failed[0].error_message.lower()
    p.shutdown()
    db.close()


def test_pipeline_republishes_device_fallbacks(tmp_path) -> None:
    """When source.device_fallbacks is non-empty, publish RecordingDeviceFallback events."""
    paths, db, settings = _make_env(tmp_path)
    bus = EventBus()
    fallbacks: list[RecordingDeviceFallback] = []
    bus.subscribe(RecordingDeviceFallback, fallbacks.append)

    mic = np.zeros(48_000, dtype=np.float32)
    loop = np.zeros(48_000, dtype=np.float32)
    source = FakeAudioSource(mic, loop)
    source.device_fallbacks = [("microphone", "Vanished Mic")]

    p = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: source,
        meeting_watcher=None,
        transcriber=_NoopTranscriber(),
        summarizer=_NoopSummarizer(),
    )
    rid = p.start_manual(detected_title="t")
    source.run_until_exhausted()
    p.stop_manual()
    p.shutdown()
    db.close()

    assert len(fallbacks) == 1
    assert fallbacks[0].channel == "microphone"
    assert fallbacks[0].requested_name == "Vanished Mic"
    assert fallbacks[0].recording_id == rid
