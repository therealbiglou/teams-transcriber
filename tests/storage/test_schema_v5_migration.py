from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import build_database
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus,
)
from teams_transcriber.storage.recordings import RecordingRepo


def test_v5_migration_adds_chat_messages_with_cascade(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    with db.connect() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "chat_messages" in names
        conn.execute(
            "INSERT INTO chat_messages (recording_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)", (rec.id, "user", "hi", "2026-06-09T10:00:00Z"),
        )
        conn.execute(
            "INSERT INTO chat_messages (recording_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)", (rec.id, "assistant", "hello", "2026-06-09T10:00:01Z"),
        )
        conn.commit()
    RecordingRepo(db).delete(rec.id)
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
    assert n == 0
    db.close()
