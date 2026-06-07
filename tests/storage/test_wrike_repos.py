import pytest

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import build_database
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.wrike import (
    WrikeSyncRepo, WrikeTaskRepo, WrikeSyncRow, WrikeTaskRow,
)


@pytest.fixture
def db_with_recording(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-07T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    yield db, rec.id
    db.close()


def test_wrike_sync_upsert_get_update(db_with_recording):
    db, rid = db_with_recording
    repo = WrikeSyncRepo(db)
    repo.upsert(rid, status="pending")
    row = repo.get(rid)
    assert row is not None and row.status == "pending" and row.folder_id is None
    repo.update(rid, status="synced", folder_id="F1")
    row = repo.get(rid)
    assert row.status == "synced" and row.folder_id == "F1"


def test_wrike_sync_list_pending_includes_failed(db_with_recording):
    db, rid = db_with_recording
    WrikeSyncRepo(db).upsert(rid, status="failed", error_message="boom")
    pending = WrikeSyncRepo(db).list_pending_or_failed()
    assert any(r.recording_id == rid for r in pending)


def test_wrike_task_insert_and_list(db_with_recording):
    db, rid = db_with_recording
    repo = WrikeTaskRepo(db)
    repo.insert(WrikeTaskRow(
        id=None, recording_id=rid, kind="my", todo_index=0,
        wrike_task_id="T1", wrike_folder_id="F1",
        created_at="2026-06-07T10:00:00Z", last_synced_done=False,
    ))
    rows = repo.list_for_recording(rid)
    assert len(rows) == 1 and rows[0].wrike_task_id == "T1"
    assert repo.get(rid, "my", 0).wrike_task_id == "T1"
    assert repo.get(rid, "my", 1) is None


def test_wrike_task_update_last_synced(db_with_recording):
    db, rid = db_with_recording
    repo = WrikeTaskRepo(db)
    repo.insert(WrikeTaskRow(
        id=None, recording_id=rid, kind="my", todo_index=0,
        wrike_task_id="T1", wrike_folder_id="F1",
        created_at="2026-06-07T10:00:00Z", last_synced_done=False,
    ))
    repo.set_last_synced_done(rid, "my", 0, True)
    assert repo.get(rid, "my", 0).last_synced_done is True
