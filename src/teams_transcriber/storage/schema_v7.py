"""v7: phone_imports — UID ledger for recordings imported from the Android app."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS = (
    """
    CREATE TABLE phone_imports (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        uid           TEXT NOT NULL UNIQUE,
        recording_id  INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        source        TEXT NOT NULL,
        imported_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_phone_imports_recording ON phone_imports(recording_id)",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V7 = Migration(version=7, name="add phone_imports ledger", apply=_apply)
