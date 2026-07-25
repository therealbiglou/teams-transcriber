from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import build_database
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.todos import TodoStateRepo


def test_v8_migration_adds_toggled_at_column(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    with db.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(todo_state)").fetchall()}
        assert "toggled_at" in cols
    db.close()


def test_v8_migration_preserves_existing_rows_with_null_toggled_at(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-07-24T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    repo = TodoStateRepo(db)
    # seed() is the "re-summarization reseeds a todo" path and never
    # touches toggled_at on a fresh insert (unlike upsert/mark_done, which
    # stamp it on every write) -- this is how a genuinely pre-v8-shaped row
    # (toggled_at never set) is produced against a fully migrated db.
    repo.seed(rec.id, todo_index=0, task_text="Write spec")
    items = repo.list_for_recording(rec.id)
    assert len(items) == 1
    assert items[0].toggled_at is None
    db.close()
