"""v8: todo_state.toggled_at — durable LWW baseline that survives un-checking."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE todo_state ADD COLUMN toggled_at TEXT")


SCHEMA_V8 = Migration(version=8, name="add todo_state.toggled_at", apply=_apply)
