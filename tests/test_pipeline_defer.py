from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from teams_transcriber.audio.source import FakeAudioSource
from teams_transcriber.config import load_settings
from teams_transcriber.events import EventBus, RecordingFinalized
from teams_transcriber.paths import AppPaths
from teams_transcriber.pipeline import Pipeline
from teams_transcriber.storage import (
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    build_database,
)
from teams_transcriber.storage.models import RecordingStatus as _RS


def test_waiting_for_notes_status_exists():
    assert _RS.WAITING_FOR_NOTES.value == "waiting_for_notes"


class _NoopTranscriber:
    def transcribe(self, rid: int) -> None:  # pragma: no cover - not invoked
        pass


class _NoopSummarizer:
    def summarize(self, rid: int, *, api_key) -> None:  # pragma: no cover
        pass


def _build_pipeline(tmp_path: Path, gate: Callable[[int], bool] | None) -> tuple[Pipeline, object]:
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    settings = load_settings(paths)
    bus = EventBus()
    mic = np.zeros(16, dtype=np.float32)
    loop = np.zeros(16, dtype=np.float32)
    pipe = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: FakeAudioSource(mic, loop),
        meeting_watcher=None,
        transcriber=_NoopTranscriber(),  # type: ignore[arg-type]
        summarizer=_NoopSummarizer(),  # type: ignore[arg-type]
        processing_gate=gate,
    )
    return pipe, db


def _make_recording(db, status: RecordingStatus) -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-26T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=10_000,
        status=status, error_message=None,
    ))
    return rec.id


def test_finalized_defers_when_gate_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipe, db = _build_pipeline(tmp_path, gate=lambda rid: True)
    rid = _make_recording(db, RecordingStatus.TRANSCRIBING)

    submitted: list[int] = []
    monkeypatch.setattr(pipe, "_submit_post_processing", submitted.append)

    pipe._on_recording_finalized(RecordingFinalized(recording_id=rid, duration_ms=1000))

    assert submitted == []
    assert RecordingRepo(db).get(rid).status == RecordingStatus.WAITING_FOR_NOTES
    assert rid in pipe._deferred
    db.close()


def test_finalized_submits_when_gate_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipe, db = _build_pipeline(tmp_path, gate=lambda rid: False)
    rid = _make_recording(db, RecordingStatus.TRANSCRIBING)

    submitted: list[int] = []
    monkeypatch.setattr(pipe, "_submit_post_processing", submitted.append)

    pipe._on_recording_finalized(RecordingFinalized(recording_id=rid, duration_ms=1000))

    assert submitted == [rid]
    assert rid not in pipe._deferred
    db.close()


def test_release_processing_submits_and_clears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipe, db = _build_pipeline(tmp_path, gate=lambda rid: True)
    rid = _make_recording(db, RecordingStatus.TRANSCRIBING)

    submitted: list[int] = []
    monkeypatch.setattr(pipe, "_submit_post_processing", submitted.append)

    pipe._on_recording_finalized(RecordingFinalized(recording_id=rid, duration_ms=1000))
    assert submitted == []
    assert rid in pipe._deferred

    pipe.release_processing(rid)

    assert submitted == [rid]
    assert rid not in pipe._deferred
    assert RecordingRepo(db).get(rid).status == RecordingStatus.TRANSCRIBING
    db.close()


def test_release_before_finalize_does_not_strand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TOCTOU race: notes window closes (release) before RecordingFinalized arrives.

    The release marks an early-release flag; when finalize runs it must NOT defer
    (which would strand the recording in WAITING_FOR_NOTES) — it must process it.
    """
    pipe, db = _build_pipeline(tmp_path, gate=lambda rid: True)
    rid = _make_recording(db, RecordingStatus.TRANSCRIBING)

    submitted: list[int] = []
    monkeypatch.setattr(pipe, "_submit_post_processing", lambda rid: submitted.append(rid))

    # Release arrives first (notes window already closed) — no deferral stored yet.
    pipe.release_processing(rid)
    # Now the finalize handler runs.
    pipe._on_recording_finalized(RecordingFinalized(recording_id=rid, duration_ms=1000))

    assert submitted == [rid]  # processed, not deferred
    assert rid not in pipe._deferred
    assert RecordingRepo(db).get(rid).status != RecordingStatus.WAITING_FOR_NOTES
    db.close()


def test_release_unknown_id_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipe, db = _build_pipeline(tmp_path, gate=lambda rid: True)

    submitted: list[int] = []
    monkeypatch.setattr(pipe, "_submit_post_processing", submitted.append)

    pipe.release_processing(9999)

    assert submitted == []
    db.close()


def test_no_gate_submits_directly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipe, db = _build_pipeline(tmp_path, gate=None)
    rid = _make_recording(db, RecordingStatus.TRANSCRIBING)

    submitted: list[int] = []
    monkeypatch.setattr(pipe, "_submit_post_processing", submitted.append)

    pipe._on_recording_finalized(RecordingFinalized(recording_id=rid, duration_ms=1000))

    assert submitted == [rid]
    assert rid not in pipe._deferred
    db.close()


def test_recovery_processes_waiting_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """WAITING_FOR_NOTES rows are submitted for processing on startup (no notes window can be open)."""
    pipe, db = _build_pipeline(tmp_path, gate=lambda rid: False)

    submitted: list[int] = []
    monkeypatch.setattr(pipe, "_submit_post_processing", lambda rid: submitted.append(rid))

    rid = _make_recording(db, RecordingStatus.TRANSCRIBING)
    RecordingRepo(db).update_status(rid, RecordingStatus.WAITING_FOR_NOTES)

    pipe._recover_stuck_recordings()

    assert submitted == [rid]
    assert RecordingRepo(db).get(rid).status == RecordingStatus.TRANSCRIBING
    db.close()


def test_pipeline_import_audio_file_copies_creates_row_and_submits(tmp_path, monkeypatch):
    """Pipeline.import_audio_file wraps importer + executor submit."""
    import wave
    pipe, db = _build_pipeline(tmp_path, gate=None)
    submitted: list[int] = []
    monkeypatch.setattr(pipe, "_submit_post_processing", lambda rid: submitted.append(rid))

    src = tmp_path / "external" / "imported-meet.wav"
    src.parent.mkdir(parents=True)
    n = int(0.5 * 16_000)
    t = np.linspace(0, 0.5, n, endpoint=False, dtype=np.float32)
    samples = (0.25 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16_000)
        w.writeframes(samples.tobytes())

    rid = pipe.import_audio_file(str(src))

    assert rid > 0
    assert submitted == [rid]
    rec = RecordingRepo(db).get(rid)
    assert rec is not None
    assert rec.status == RecordingStatus.TRANSCRIBING
    assert rec.audio_path is not None
    assert Path(rec.audio_path).is_file()
    db.close()
