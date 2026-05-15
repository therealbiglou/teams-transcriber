from datetime import UTC, datetime, timedelta
from pathlib import Path

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import Recording, RecordingSource, RecordingStatus
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.retention import AudioRetentionPruner


def _isoz(dt: datetime) -> str:
    return dt.isoformat()


def _make_recording(
    db: Database, *, started_at: datetime, audio_path: Path | None
) -> int:
    rec = RecordingRepo(db).create(
        Recording(
            id=None,
            started_at=_isoz(started_at),
            ended_at=_isoz(started_at + timedelta(minutes=30)),
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title="X",
            audio_path=str(audio_path) if audio_path else None,
            audio_deleted_at=None,
            duration_ms=30 * 60 * 1000,
            status=RecordingStatus.DONE,
            error_message=None,
        )
    )
    assert rec.id is not None
    return rec.id


def test_prune_deletes_old_audio(db: Database, tmp_path: Path) -> None:
    old_audio = tmp_path / "old.opus"
    old_audio.write_bytes(b"x" * 1024)
    new_audio = tmp_path / "new.opus"
    new_audio.write_bytes(b"y" * 1024)

    now = datetime.now(UTC)
    old_id = _make_recording(db, started_at=now - timedelta(days=45), audio_path=old_audio)
    new_id = _make_recording(db, started_at=now - timedelta(days=5), audio_path=new_audio)

    pruner = AudioRetentionPruner(db, retention_days=30, now=lambda: now)
    report = pruner.run()

    assert not old_audio.exists()
    assert new_audio.exists()
    assert report.deleted_count == 1
    assert report.skipped_count == 1

    repo = RecordingRepo(db)
    old = repo.get(old_id)
    assert old is not None
    assert old.audio_path is None
    assert old.audio_deleted_at is not None

    new = repo.get(new_id)
    assert new is not None
    assert new.audio_path == str(new_audio)


def test_prune_ignores_already_pruned_recordings(db: Database, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    old_id = _make_recording(db, started_at=now - timedelta(days=45), audio_path=None)
    pruner = AudioRetentionPruner(db, retention_days=30, now=lambda: now)
    report = pruner.run()
    assert report.deleted_count == 0
    assert report.skipped_count == 0  # null-audio rows aren't even considered
    assert RecordingRepo(db).get(old_id) is not None  # row still exists


def test_prune_handles_missing_file_gracefully(db: Database, tmp_path: Path) -> None:
    """If the audio file is already gone on disk, we still null out the DB column."""
    now = datetime.now(UTC)
    ghost = tmp_path / "ghost.opus"  # never written
    rec_id = _make_recording(db, started_at=now - timedelta(days=45), audio_path=ghost)

    pruner = AudioRetentionPruner(db, retention_days=30, now=lambda: now)
    report = pruner.run()
    assert report.deleted_count == 0
    assert report.missing_count == 1

    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.audio_path is None
    assert rec.audio_deleted_at is not None


def test_prune_does_not_touch_recordings_currently_in_progress(
    db: Database, tmp_path: Path
) -> None:
    """A recording with status='recording' must not be pruned even if started long ago."""
    audio = tmp_path / "live.opus"
    audio.write_bytes(b"x")
    now = datetime.now(UTC)
    rec = RecordingRepo(db).create(
        Recording(
            id=None,
            started_at=_isoz(now - timedelta(days=45)),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title="X",
            audio_path=str(audio),
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    pruner = AudioRetentionPruner(db, retention_days=30, now=lambda: now)
    report = pruner.run()
    assert report.deleted_count == 0
    assert audio.exists()
    again = RecordingRepo(db).get(rec.id)  # type: ignore[arg-type]
    assert again is not None
    assert again.audio_path == str(audio)


def test_retention_days_zero_disables_pruning(db: Database, tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x")
    now = datetime.now(UTC)
    _make_recording(db, started_at=now - timedelta(days=365), audio_path=audio)
    pruner = AudioRetentionPruner(db, retention_days=0, now=lambda: now)
    report = pruner.run()
    assert report.deleted_count == 0
    assert audio.exists()
