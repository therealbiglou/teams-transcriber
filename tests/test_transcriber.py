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
