"""v10: drop todo_state — to-do done-state was only ever read by the removed
in-app views. Wrike tasks are the checkboxes now, so the data has no consumer."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS = (
    "DROP TABLE IF EXISTS todo_state",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V10 = Migration(version=10, name="drop todo_state", apply=_apply)
