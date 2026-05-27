"""Schema v3: allow the `waiting_for_notes` status on the `recordings` table.

The v1 CHECK constraint on `recordings.status` predates the deferred-processing
feature (post-processing waits for an open notes window to close). SQLite cannot
ALTER a CHECK constraint in place, so we rebuild the table with the expanded set
of allowed values, preserving every column added through v2 (`manual_notes`) and
all existing rows.

This runs with `foreign_keys = OFF` (the MigrationRunner toggles it around each
migration) so that dropping the old `recordings` table does NOT cascade-delete
the dependent transcript_segments / summaries / todo_state rows, and the RENAME
does not rewrite their foreign-key references.
"""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE recordings_new (
        id              INTEGER PRIMARY KEY,
        started_at      TEXT    NOT NULL,
        ended_at        TEXT,
        source          TEXT    NOT NULL CHECK (source IN ('teams', 'manual')),
        detected_title  TEXT,
        display_title   TEXT,
        audio_path      TEXT,
        audio_deleted_at TEXT,
        duration_ms     INTEGER,
        status          TEXT    NOT NULL CHECK (status IN (
                            'recording',
                            'transcribing',
                            'summarizing',
                            'done',
                            'recording_failed',
                            'transcription_failed',
                            'summary_failed',
                            'waiting_for_notes'
                        )),
        error_message   TEXT,
        manual_notes    TEXT
    )
    """,
    """
    INSERT INTO recordings_new (
        id, started_at, ended_at, source, detected_title, display_title,
        audio_path, audio_deleted_at, duration_ms, status, error_message,
        manual_notes
    )
    SELECT
        id, started_at, ended_at, source, detected_title, display_title,
        audio_path, audio_deleted_at, duration_ms, status, error_message,
        manual_notes
    FROM recordings
    """,
    "DROP TABLE recordings",
    "ALTER TABLE recordings_new RENAME TO recordings",
    # Recreate the indexes that lived on the original table.
    "CREATE INDEX recordings_started_at_idx ON recordings (started_at DESC)",
    "CREATE INDEX recordings_status_idx     ON recordings (status)",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V3 = Migration(version=3, name="allow waiting_for_notes status", apply=_apply)
