from datetime import UTC, datetime

import pytest

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import Recording, RecordingSource, RecordingStatus
from teams_transcriber.storage.recordings import RecordingRepo


def _now() -> str:
    return datetime.now(UTC).isoformat()


def test_create_returns_recording_with_id(db: Database) -> None:
    repo = RecordingRepo(db)
    rec = repo.create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="Meeting | Microsoft Teams",
            display_title=None,
            audio_path="C:/tmp/a.opus",
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    assert rec.id is not None
    assert rec.detected_title == "Meeting | Microsoft Teams"


def test_get_returns_none_for_missing(db: Database) -> None:
    repo = RecordingRepo(db)
    assert repo.get(999) is None


def test_get_returns_existing(db: Database) -> None:
    repo = RecordingRepo(db)
    created = repo.create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.MANUAL,
            detected_title=None,
            display_title="Manual",
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    fetched = repo.get(created.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.source == RecordingSource.MANUAL
    assert fetched.display_title == "Manual"


def test_update_status_and_error(db: Database) -> None:
    repo = RecordingRepo(db)
    created = repo.create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title=None,
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    repo.update_status(created.id, RecordingStatus.SUMMARY_FAILED, error_message="api down")  # type: ignore[arg-type]
    again = repo.get(created.id)  # type: ignore[arg-type]
    assert again is not None
    assert again.status == RecordingStatus.SUMMARY_FAILED
    assert again.error_message == "api down"


def test_finalize_sets_ended_and_duration(db: Database) -> None:
    repo = RecordingRepo(db)
    created = repo.create(
        Recording(
            id=None,
            started_at="2026-05-14T10:00:00+00:00",
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title=None,
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    repo.finalize(
        created.id,  # type: ignore[arg-type]
        ended_at="2026-05-14T10:05:00+00:00",
        duration_ms=300_000,
    )
    again = repo.get(created.id)  # type: ignore[arg-type]
    assert again is not None
    assert again.ended_at == "2026-05-14T10:05:00+00:00"
    assert again.duration_ms == 300_000


def test_set_display_title(db: Database) -> None:
    repo = RecordingRepo(db)
    created = repo.create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="Meeting | Microsoft Teams",
            display_title=None,
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    repo.set_display_title(created.id, "Q2 roadmap sync")  # type: ignore[arg-type]
    again = repo.get(created.id)  # type: ignore[arg-type]
    assert again is not None
    assert again.display_title == "Q2 roadmap sync"


def test_list_recent_orders_by_started_desc(db: Database) -> None:
    repo = RecordingRepo(db)
    for i in range(3):
        repo.create(
            Recording(
                id=None,
                started_at=f"2026-05-1{i}T10:00:00+00:00",
                ended_at=None,
                source=RecordingSource.TEAMS,
                detected_title=f"Meeting {i}",
                display_title=None,
                audio_path=None,
                audio_deleted_at=None,
                duration_ms=None,
                status=RecordingStatus.DONE,
                error_message=None,
            )
        )
    recents = repo.list_recent(limit=10)
    assert [r.detected_title for r in recents] == ["Meeting 2", "Meeting 1", "Meeting 0"]


def test_delete_removes_row(db: Database) -> None:
    repo = RecordingRepo(db)
    created = repo.create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title=None,
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    repo.delete(created.id)  # type: ignore[arg-type]
    assert repo.get(created.id) is None  # type: ignore[arg-type]


def test_create_rejects_invalid_source(db: Database) -> None:
    import sqlite3
    repo = RecordingRepo(db)  # noqa: F841 — repo unused; we exercise the CHECK directly
    # Sneak around the enum to exercise the CHECK constraint at the SQL level.
    with pytest.raises(sqlite3.IntegrityError), db.connect() as conn:
        conn.execute(
            "INSERT INTO recordings (started_at, source, status) VALUES (?, ?, ?)",
            (_now(), "invalid", "recording"),
        )


def test_list_by_status_filters_and_orders(db: Database) -> None:
    repo = RecordingRepo(db)
    for i, status in enumerate([
        RecordingStatus.RECORDING,
        RecordingStatus.DONE,
        RecordingStatus.RECORDING,
        RecordingStatus.SUMMARY_FAILED,
    ]):
        repo.create(
            Recording(
                id=None,
                started_at=f"2026-05-1{i}T10:00:00+00:00",
                ended_at=None,
                source=RecordingSource.TEAMS,
                detected_title=f"Meeting {i}",
                display_title=None,
                audio_path=None,
                audio_deleted_at=None,
                duration_ms=None,
                status=status,
                error_message=None,
            )
        )
    recording_only = repo.list_by_status(RecordingStatus.RECORDING)
    assert [r.detected_title for r in recording_only] == ["Meeting 2", "Meeting 0"]
    assert repo.list_by_status(RecordingStatus.TRANSCRIBING) == []
