from teams_transcriber.storage import ALL_MIGRATIONS
from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus,
)
from teams_transcriber.storage.recordings import RecordingRepo

# todo_state exists only through schema v8 (added toggled_at) up to v9; v10
# drops the table entirely. These tests exercise the historical migration
# chain in isolation — v1..v8 — since that behavior is a permanent fact about
# migrations already applied to users' databases, independent of the table's
# later removal.
_MIGRATIONS_THROUGH_V8 = ALL_MIGRATIONS[:8]


def test_v8_migration_adds_toggled_at_column(tmp_path):
    db = Database(tmp_path / "t.db", migrations=_MIGRATIONS_THROUGH_V8)
    db.initialize()
    with db.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(todo_state)").fetchall()}
        assert "toggled_at" in cols
    db.close()


def test_v8_migration_preserves_existing_rows_with_null_toggled_at(tmp_path):
    db = Database(tmp_path / "t.db", migrations=_MIGRATIONS_THROUGH_V8)
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-07-24T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    # seed() is the "re-summarization reseeds a todo" path and never
    # touches toggled_at on a fresh insert (unlike upsert/mark_done, which
    # stamp it on every write) -- this is how a genuinely pre-v8-shaped row
    # (toggled_at never set) is produced against a fully migrated db.
    # TodoStateRepo is gone (schema v10 drops todo_state), so insert directly.
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO todo_state (recording_id, todo_index, task_text, done, done_at, toggled_at)
            VALUES (?, 0, 'Write spec', 0, NULL, NULL)
            """,
            (rec.id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT toggled_at FROM todo_state WHERE recording_id = ? AND todo_index = 0",
            (rec.id,),
        ).fetchone()
    assert row["toggled_at"] is None
    db.close()
