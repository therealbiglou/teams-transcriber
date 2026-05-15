from datetime import UTC, datetime

import pytest

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import (
    Recording,
    RecordingSource,
    RecordingStatus,
    TodoState,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.todos import TodoStateRepo


def _now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def recording_id(db: Database) -> int:
    rec = RecordingRepo(db).create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title="X",
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.DONE,
            error_message=None,
        )
    )
    assert rec.id is not None
    return rec.id


def test_upsert_inserts_new_state(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.upsert(recording_id, todo_index=0, task_text="Write spec", done=False)
    items = repo.list_for_recording(recording_id)
    assert len(items) == 1
    assert isinstance(items[0], TodoState)
    assert items[0].task_text == "Write spec"
    assert items[0].done is False
    assert items[0].done_at is None


def test_mark_done_sets_timestamp(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.upsert(recording_id, todo_index=0, task_text="Write spec", done=False)
    repo.mark_done(recording_id, todo_index=0, done=True)
    items = repo.list_for_recording(recording_id)
    assert items[0].done is True
    assert items[0].done_at is not None


def test_mark_undone_clears_timestamp(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.upsert(recording_id, todo_index=0, task_text="Write spec", done=False)
    repo.mark_done(recording_id, todo_index=0, done=True)
    repo.mark_done(recording_id, todo_index=0, done=False)
    items = repo.list_for_recording(recording_id)
    assert items[0].done is False
    assert items[0].done_at is None


def test_upsert_is_idempotent_on_index(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.upsert(recording_id, todo_index=0, task_text="Write spec", done=False)
    repo.upsert(recording_id, todo_index=0, task_text="Write spec v2", done=False)
    items = repo.list_for_recording(recording_id)
    assert len(items) == 1
    assert items[0].task_text == "Write spec v2"


def test_mark_done_creates_row_if_missing(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.mark_done(recording_id, todo_index=2, done=True, task_text="Inserted lazily")
    items = repo.list_for_recording(recording_id)
    assert len(items) == 1
    assert items[0].todo_index == 2
    assert items[0].done is True


def test_mark_done_requires_task_text_if_row_missing(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    with pytest.raises(ValueError, match="task_text"):
        repo.mark_done(recording_id, todo_index=0, done=True)


def test_recording_delete_cascades(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.upsert(recording_id, todo_index=0, task_text="X", done=True)
    RecordingRepo(db).delete(recording_id)
    assert repo.list_for_recording(recording_id) == []
