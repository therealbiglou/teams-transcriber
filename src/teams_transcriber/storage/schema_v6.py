"""Schema v6: rebuild wrike_tasks with widened kind CHECK + format/assignee_id.

`wrike_tasks.kind` had `CHECK (kind IN ('my', 'other'))` from v4. Phase 13
needs summaries, decisions, follow-ups too, and each row needs its own format
(task | comment) and assignee. SQLite can't ALTER a CHECK, so we follow the
schema v3 rebuild precedent: CREATE new, INSERT SELECT, DROP old, RENAME. The
MigrationRunner toggles foreign_keys=OFF around the migration so the DROP
doesn't cascade-delete child rows and the RENAME doesn't rewrite FK refs.
"""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE wrike_tasks_new (
        id                INTEGER PRIMARY KEY,
        recording_id      INTEGER NOT NULL
                          REFERENCES recordings(id) ON DELETE CASCADE,
        kind              TEXT NOT NULL CHECK (kind IN
                              ('my', 'other', 'my_todo', 'action_other',
                               'summary', 'decisions', 'follow_up')),
        todo_index        INTEGER NOT NULL,
        wrike_task_id     TEXT NOT NULL,
        wrike_folder_id   TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        last_synced_done  INTEGER NOT NULL DEFAULT 0,
        format            TEXT NOT NULL DEFAULT 'task'
                          CHECK (format IN ('task', 'comment')),
        assignee_id       TEXT,
        UNIQUE (recording_id, kind, todo_index)
    )
    """,
    """
    INSERT INTO wrike_tasks_new
        (id, recording_id, kind, todo_index, wrike_task_id, wrike_folder_id,
         created_at, last_synced_done, format, assignee_id)
    SELECT id, recording_id, kind, todo_index, wrike_task_id, wrike_folder_id,
           created_at, last_synced_done, 'task', NULL
    FROM wrike_tasks
    """,
    "DROP TABLE wrike_tasks",
    "ALTER TABLE wrike_tasks_new RENAME TO wrike_tasks",
    "CREATE INDEX wrike_tasks_recording_idx ON wrike_tasks (recording_id)",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V6 = Migration(version=6, name="rebuild wrike_tasks for multi-dest", apply=_apply)
