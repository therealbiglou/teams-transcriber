from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from teams_transcriber.audio.source import FakeAudioSource
from teams_transcriber.config import Settings
from teams_transcriber.events import (
    EventBus,
    RecordingFinalized,
    RecordingStarted,
)
from teams_transcriber.paths import AppPaths
from teams_transcriber.recorder import Recorder
from teams_transcriber.storage import (
    RecordingRepo,
    RecordingStatus,
    build_database,
)


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT")
    p.ensure_dirs()
    return p


@pytest.fixture
def db_and_repo(paths: AppPaths):
    db = build_database(paths.db_path)
    db.initialize()
    repo = RecordingRepo(db)
    yield db, repo
    db.close()


def _make_source(seconds: float) -> FakeAudioSource:
    n = int(seconds * 16_000)
    t = np.linspace(0, seconds, n, endpoint=False, dtype=np.float32)
    mic = 0.25 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    loop = 0.25 * np.sin(2 * np.pi * 880 * t).astype(np.float32)
    return FakeAudioSource(mic_samples=mic, loopback_samples=loop)


def test_recorder_creates_recording_and_finalizes(paths, db_and_repo) -> None:
    db, repo = db_and_repo
    bus = EventBus()
    settings = Settings()

    started: list[RecordingStarted] = []
    finalized: list[RecordingFinalized] = []
    bus.subscribe(RecordingStarted, started.append)
    bus.subscribe(RecordingFinalized, finalized.append)

    source = _make_source(seconds=1.5)
    recorder = Recorder(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source=source,
    )

    rec_id = recorder.start(source_type="manual", detected_title=None)
    # Let the capture thread run.
    source.run_until_exhausted()
    recorder.stop()

    assert len(started) == 1
    assert started[0].recording_id == rec_id
    assert len(finalized) == 1
    assert finalized[0].recording_id == rec_id
    assert finalized[0].duration_ms >= 1000

    row = repo.get(rec_id)
    assert row is not None
    assert row.status == RecordingStatus.TRANSCRIBING
    assert row.duration_ms == finalized[0].duration_ms
    assert row.audio_path is not None
    assert Path(row.audio_path).exists()


def test_recorder_records_to_distinct_paths(paths, db_and_repo) -> None:
    db, _repo = db_and_repo
    bus = EventBus()
    settings = Settings()
    src1 = _make_source(seconds=0.5)
    src2 = _make_source(seconds=0.5)
    r1 = Recorder(bus=bus, db=db, paths=paths, settings=settings, audio_source=src1)
    r1.start(source_type="manual", detected_title=None)
    src1.run_until_exhausted()
    r1.stop()
    r2 = Recorder(bus=bus, db=db, paths=paths, settings=settings, audio_source=src2)
    r2.start(source_type="manual", detected_title=None)
    src2.run_until_exhausted()
    r2.stop()
    repo = RecordingRepo(db)
    recs = repo.list_recent()
    assert len({r.audio_path for r in recs}) == 2


def test_recorder_cancel_deletes_file_and_row(paths, db_and_repo) -> None:
    db, repo = db_and_repo
    bus = EventBus()
    settings = Settings()
    source = _make_source(seconds=1.0)
    recorder = Recorder(bus=bus, db=db, paths=paths, settings=settings, audio_source=source)
    rec_id = recorder.start(source_type="teams", detected_title="X")
    source.run_until_samples(int(0.3 * 16_000))
    recorder.cancel()

    assert repo.get(rec_id) is None
    # No audio file should remain.
    audio_files = list(paths.audio_dir.glob("*.opus"))
    assert audio_files == []
