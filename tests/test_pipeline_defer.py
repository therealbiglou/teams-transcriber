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
