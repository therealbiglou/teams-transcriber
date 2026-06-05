from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from teams_transcriber.audio.opus_writer import SAMPLE_RATE, OpusWriter
from teams_transcriber.config import Settings
from teams_transcriber.events import EventBus, TranscriptionComplete
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    Channel,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    TranscriptRepo,
    build_database,
)
from teams_transcriber.transcriber import Transcriber

# --- A stand-in for faster_whisper.WhisperModel.transcribe ------------

@dataclass
class _FakeWord:
    word: str
    start: float
    end: float


@dataclass
class _FakeSegment:
    start: float
    end: float
    text: str
    words: list[_FakeWord]


@dataclass
class _FakeInfo:
    language: str = "en"
    duration: float = 5.0


class FakeWhisperModel:
    """Returns canned segments based on the audio file name."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def transcribe(self, path: str, *args: Any, **kwargs: Any
                   ) -> tuple[Iterator[_FakeSegment], _FakeInfo]:
        segs = [
            _FakeSegment(0.0, 1.5, "Hello there", words=[]),
            _FakeSegment(1.5, 3.0, "How are you", words=[]),
            _FakeSegment(3.0, 4.2, "I am well thanks", words=[]),
        ]
        return iter(segs), _FakeInfo()


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT")
    p.ensure_dirs()
    return p


@pytest.fixture
def db_with_recording(paths: AppPaths):
    db = build_database(paths.db_path)
    db.initialize()
    audio = paths.audio_dir / "fake.opus"

    # Write a tiny real 2-channel Opus so the splitter has something to work with.
    n = int(0.5 * SAMPLE_RATE)
    t = np.linspace(0, 0.5, n, endpoint=False, dtype=np.float32)
    pcm = np.stack([
        (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32),
        (0.1 * np.sin(2 * np.pi * 880 * t)).astype(np.float32),
    ], axis=1)
    w = OpusWriter(audio, channels=2, bitrate_kbps=24)
    w.write_chunk(pcm)
    w.close()

    rec = RecordingRepo(db).create(Recording(
        id=None,
        started_at="2026-05-14T10:00:00+00:00",
        ended_at="2026-05-14T10:00:05+00:00",
        source=RecordingSource.MANUAL,
        detected_title=None,
        display_title=None,
        audio_path=str(audio),
        audio_deleted_at=None,
        duration_ms=5000,
        status=RecordingStatus.TRANSCRIBING,
        error_message=None,
    ))
    yield db, rec.id
    db.close()


def test_transcribe_writes_segments_and_emits_event(db_with_recording, paths) -> None:
    db, rec_id = db_with_recording
    bus = EventBus()
    settings = Settings()
    received: list[TranscriptionComplete] = []
    bus.subscribe(TranscriptionComplete, received.append)

    transcriber = Transcriber(
        bus=bus, db=db, settings=settings,
        model_factory=lambda *_a, **_kw: FakeWhisperModel(),
    )
    transcriber.transcribe(rec_id)

    segments = TranscriptRepo(db).list_for_recording(rec_id)
    # We transcribe TWO channels with the same fake model, so 6 segments total.
    assert len(segments) == 6
    me_segs = [s for s in segments if s.channel == Channel.ME]
    other_segs = [s for s in segments if s.channel == Channel.OTHERS]
    assert len(me_segs) == 3
    assert len(other_segs) == 3
    assert me_segs[0].text == "Hello there"

    assert len(received) == 1
    assert received[0].segment_count == 6

    repo = RecordingRepo(db)
    rec = repo.get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.SUMMARIZING


def test_transcribe_mono_audio_runs_single_pass_labeled_me(paths) -> None:
    """Imported (mono) audio: one Whisper pass, all segments tagged Channel.ME."""
    import wave
    db = build_database(paths.db_path)
    db.initialize()
    audio = paths.audio_dir / "imported-mono.wav"
    rate = 16_000
    n = int(0.5 * rate)
    t = np.linspace(0, 0.5, n, endpoint=False, dtype=np.float32)
    samples = (0.25 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(samples.tobytes())

    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-05T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="Imported",
        display_title="Imported", audio_path=str(audio), audio_deleted_at=None,
        duration_ms=500, status=RecordingStatus.TRANSCRIBING, error_message=None,
    ))

    bus = EventBus()
    received: list[TranscriptionComplete] = []
    bus.subscribe(TranscriptionComplete, received.append)
    Transcriber(
        bus=bus, db=db, settings=Settings(),
        model_factory=lambda *_a, **_kw: FakeWhisperModel(),
    ).transcribe(rec.id)

    segments = TranscriptRepo(db).list_for_recording(rec.id)
    # ONE Whisper pass over mono => 3 fake segments, all ME.
    assert len(segments) == 3
    assert {s.channel for s in segments} == {Channel.ME}
    assert RecordingRepo(db).get(rec.id).status == RecordingStatus.SUMMARIZING
    assert received and received[0].segment_count == 3
    db.close()


def test_transcribe_marks_failed_when_audio_missing(db_with_recording, paths) -> None:
    db, rec_id = db_with_recording
    # Delete the audio file under the Transcriber's feet.
    rec = RecordingRepo(db).get(rec_id)
    assert rec and rec.audio_path
    Path(rec.audio_path).unlink()

    bus = EventBus()
    transcriber = Transcriber(
        bus=bus, db=db, settings=Settings(),
        model_factory=lambda *_a, **_kw: FakeWhisperModel(),
    )
    transcriber.transcribe(rec_id)

    repo = RecordingRepo(db)
    rec = repo.get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.TRANSCRIPTION_FAILED
    assert rec.error_message is not None


def test_transcribe_marks_failed_on_model_exception(db_with_recording) -> None:
    db, rec_id = db_with_recording

    class BoomModel:
        def __init__(self, *a: Any, **kw: Any) -> None: ...
        def transcribe(self, *a: Any, **kw: Any) -> Any:
            raise RuntimeError("model exploded")

    bus = EventBus()
    transcriber = Transcriber(
        bus=bus, db=db, settings=Settings(),
        model_factory=lambda *_a, **_kw: BoomModel(),
    )
    transcriber.transcribe(rec_id)

    repo = RecordingRepo(db)
    rec = repo.get(rec_id)
    assert rec is not None
    assert rec.status == RecordingStatus.TRANSCRIPTION_FAILED
    assert "model exploded" in (rec.error_message or "")


def test_transcriber_publishes_failed_when_audio_missing(tmp_path) -> None:
    """When the recording's audio_path doesn't exist, publish TranscriptionFailed."""
    from teams_transcriber.config import load_settings
    from teams_transcriber.events import EventBus, TranscriptionFailed
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        build_database,
    )
    from teams_transcriber.transcriber import Transcriber

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    settings = load_settings(paths)
    bus = EventBus()
    received: list[TranscriptionFailed] = []
    bus.subscribe(TranscriptionFailed, received.append)

    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-20T10:00:00+00:00",
        ended_at="2026-05-20T10:05:00+00:00", source=RecordingSource.MANUAL,
        detected_title="t", display_title="t",
        audio_path=str(tmp_path / "does-not-exist.opus"),
        audio_deleted_at=None, duration_ms=300_000,
        status=RecordingStatus.TRANSCRIBING, error_message=None,
    ))
    Transcriber(bus=bus, db=db, settings=settings).transcribe(rec.id)
    db.close()

    assert len(received) == 1
    assert received[0].recording_id == rec.id
    assert "missing" in received[0].error_message.lower()


def test_transcriber_publishes_failed_on_exception(tmp_path) -> None:
    """When the model factory raises mid-transcribe, publish TranscriptionFailed."""
    from teams_transcriber.audio.opus_writer import OpusWriter
    from teams_transcriber.config import load_settings
    from teams_transcriber.events import EventBus, TranscriptionFailed
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        build_database,
    )
    from teams_transcriber.transcriber import Transcriber
    import numpy as np

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    settings = load_settings(paths)
    bus = EventBus()
    received: list[TranscriptionFailed] = []
    bus.subscribe(TranscriptionFailed, received.append)

    # Write a small real Opus file so we get past the "audio missing" check
    # and hit the model-load path.
    opus_path = tmp_path / "rec.opus"
    writer = OpusWriter(opus_path, channels=2, bitrate_kbps=64)
    pcm = np.zeros((16_000, 2), dtype=np.float32)
    writer.write_chunk(pcm)
    writer.close()

    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-20T10:00:00+00:00",
        ended_at="2026-05-20T10:00:01+00:00", source=RecordingSource.MANUAL,
        detected_title="t", display_title="t",
        audio_path=str(opus_path),
        audio_deleted_at=None, duration_ms=1_000,
        status=RecordingStatus.TRANSCRIBING, error_message=None,
    ))

    def boom(*_a, **_kw):
        raise RuntimeError("model load failed (simulated)")

    Transcriber(bus=bus, db=db, settings=settings, model_factory=boom).transcribe(rec.id)
    db.close()

    assert len(received) == 1
    assert received[0].recording_id == rec.id
    assert "simulated" in received[0].error_message.lower()
