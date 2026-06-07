from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import build_database
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus,
)
from teams_transcriber.storage.recordings import RecordingRepo


def test_v4_migration_preserves_recordings_and_adds_tables(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-07T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    with db.connect() as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = {row[0] for row in cur.fetchall()}
    assert "wrike_sync" in names
    assert "wrike_tasks" in names
    # CASCADE works.
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO wrike_sync (recording_id, folder_id, status) "
            "VALUES (?, ?, ?)", (rec.id, "F1", "synced"),
        )
        conn.execute(
            "INSERT INTO wrike_tasks "
            "(recording_id, kind, todo_index, wrike_task_id, wrike_folder_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rec.id, "my", 0, "T1", "F1", "2026-06-07T10:00:00Z"),
        )
        conn.commit()
    RecordingRepo(db).delete(rec.id)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM wrike_sync").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM wrike_tasks").fetchone()[0] == 0
    db.close()
