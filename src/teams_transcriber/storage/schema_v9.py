"""v9: wrike_projects — maps a recording to its Wrike project for idempotent
re-push (project + transcript attachment + notes comment ids)."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS = (
    """
    CREATE TABLE wrike_projects (
        recording_id     INTEGER PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
        project_id       TEXT NOT NULL,
        permalink        TEXT,
        attachment_id    TEXT,
        notes_comment_id TEXT,
        created_at       TEXT NOT NULL,
        last_pushed_at   TEXT NOT NULL
    )
    """,
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V9 = Migration(version=9, name="add wrike_projects", apply=_apply)
