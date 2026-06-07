"""Schema v4: add wrike_sync and wrike_tasks tables for the Wrike integration.

Pure CREATE additions — no existing-table CHECK changes — so we don't need the
table-rebuild dance from v3. ON DELETE CASCADE both tables from recordings so
the mappings disappear with the source recording. The Wrike tasks themselves
are left in place in Wrike (the user owns them there).
"""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE wrike_sync (
        recording_id      INTEGER PRIMARY KEY
                          REFERENCES recordings(id) ON DELETE CASCADE,
        folder_id         TEXT,
        status            TEXT NOT NULL CHECK (status IN
                              ('pending', 'synced', 'failed', 'skipped')),
        last_attempted_at TEXT,
        error_message     TEXT
    )
    """,
    """
    CREATE TABLE wrike_tasks (
        id                INTEGER PRIMARY KEY,
        recording_id      INTEGER NOT NULL
                          REFERENCES recordings(id) ON DELETE CASCADE,
        kind              TEXT NOT NULL CHECK (kind IN ('my', 'other')),
        todo_index        INTEGER NOT NULL,
        wrike_task_id     TEXT NOT NULL,
        wrike_folder_id   TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        last_synced_done  INTEGER NOT NULL DEFAULT 0,
        UNIQUE (recording_id, kind, todo_index)
    )
    """,
    "CREATE INDEX wrike_tasks_recording_idx ON wrike_tasks (recording_id)",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V4 = Migration(version=4, name="add wrike integration tables", apply=_apply)
