"""Schema v5: add chat_messages table for per-meeting Q&A with Claude.

Pure CREATE addition — no existing-table CHECK changes, no rebuild.
ON DELETE CASCADE from recordings so a meeting's chat history disappears
with the meeting. The composite index keeps `list_for_recording(rid)`
ordered by insertion (id) without a separate sort.
"""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE chat_messages (
        id           INTEGER PRIMARY KEY,
        recording_id INTEGER NOT NULL
                     REFERENCES recordings(id) ON DELETE CASCADE,
        role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content      TEXT NOT NULL,
        created_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX chat_messages_recording_idx ON chat_messages (recording_id, id)",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V5 = Migration(version=5, name="add chat_messages", apply=_apply)
