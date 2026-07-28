from __future__ import annotations

from teams_transcriber.storage import build_database


def test_todo_state_table_is_dropped(tmp_path):
    db = build_database(tmp_path / "t.db")
    db.initialize()
    with db.connect() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "todo_state" not in names


def test_other_tables_survive(tmp_path):
    db = build_database(tmp_path / "t.db")
    db.initialize()
    with db.connect() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert {"recordings", "transcript_segments", "summaries",
            "wrike_projects", "chat_messages"} <= names
    assert version == 10
