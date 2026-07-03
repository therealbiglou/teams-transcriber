"""Tests for the modified Transcriber.transcribe() finalize-or-recover behavior."""

from __future__ import annotations

import numpy as np
import pytest

from teams_transcriber.config import load_settings
from teams_transcriber.events import EventBus, TranscriptionComplete
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    Channel,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    TranscriptRepo,
    TranscriptSegment,
    build_database,
)
from teams_transcriber.transcriber import Transcriber


@pytest.fixture
def env(tmp_path):
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    settings = load_settings(paths)
    yield paths, db, settings
    db.close()


def _make_recording(db, *, duration_ms: int, audio_path: str | None) -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-18T10:00:00+00:00",
        ended_at="2026-05-18T10:05:00+00:00", source=RecordingSource.MANUAL,
        detected_title="t", display_title="t", audio_path=audio_path,
        audio_deleted_at=None, duration_ms=duration_ms,
        status=RecordingStatus.TRANSCRIBING, error_message=None,
    ))
    assert rec.id is not None
    return rec.id


def test_finalize_skips_whisper_when_live_covered(env, tmp_path) -> None:
    paths, db, settings = env
    bus = EventBus()
    completed: list[TranscriptionComplete] = []
    bus.subscribe(TranscriptionComplete, completed.append)

    rid = _make_recording(db, duration_ms=10_000, audio_path=None)
    repo = TranscriptRepo(db)
    repo.append(TranscriptSegment(
        id=None, recording_id=rid, start_ms=0, end_ms=5_000,
        channel=Channel.ME, text="first half",
    ))
    repo.append(TranscriptSegment(
        id=None, recording_id=rid, start_ms=5_000, end_ms=10_000,
        channel=Channel.OTHERS, text="second half",
    ))

    called: list[str] = []

    def _bad_model_factory(*_a, **_kw):
        called.append("loaded model — shouldn't happen on the fast path")
        raise RuntimeError("model load should not be triggered")

    t = Transcriber(bus=bus, db=db, settings=settings, model_factory=_bad_model_factory)
    t.transcribe(rid)

    assert called == []
    assert RecordingRepo(db).get(rid).status == RecordingStatus.SUMMARIZING
    assert len(completed) == 1


def test_finalize_falls_back_to_batch_when_no_segments(env, tmp_path) -> None:
    """If LiveTranscriber didn't run (or coverage < 95%), run the batch path."""
    paths, db, settings = env
    bus = EventBus()

    from teams_transcriber.audio.opus_writer import OpusWriter

    opus_path = tmp_path / "rec.opus"
    writer = OpusWriter(opus_path, channels=2, bitrate_kbps=64)
    pcm = np.zeros((16_000, 2), dtype=np.float32)
    writer.write_chunk(pcm)
    writer.close()

    rid = _make_recording(
        db, duration_ms=1_000, audio_path=str(opus_path),
    )

    def _stub_model_factory(*_a, **_kw):
        class _M:
            def transcribe(self, *_a, **_kw):
                class _Seg:
                    def __init__(self, start, end, text):
                        self.start = start; self.end = end; self.text = text
                return iter([_Seg(0.0, 1.0, "fallback hit")]), {}
        return _M()

    t = Transcriber(bus=bus, db=db, settings=settings, model_factory=_stub_model_factory)
    t.transcribe(rid)

    rows = TranscriptRepo(db).list_for_recording(rid)
    assert any(r.text == "fallback hit" for r in rows)
    assert RecordingRepo(db).get(rid).status == RecordingStatus.SUMMARIZING
