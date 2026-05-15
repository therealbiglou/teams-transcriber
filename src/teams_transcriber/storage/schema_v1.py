"""Initial schema (v1): recordings, transcript_segments, transcript_fts, summaries, todo_state."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

# Each statement runs under MigrationRunner's outer BEGIN, so partial failures roll back cleanly.
# (We avoid sqlite3.executescript() because it issues an implicit COMMIT, breaking that contract.)
_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE recordings (
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
                            'summary_failed'
                        )),
        error_message   TEXT
    )
    """,
    "CREATE INDEX recordings_started_at_idx ON recordings (started_at DESC)",
    "CREATE INDEX recordings_status_idx     ON recordings (status)",
    """
    CREATE TABLE transcript_segments (
        id              INTEGER PRIMARY KEY,
        recording_id    INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        start_ms        INTEGER NOT NULL,
        end_ms          INTEGER NOT NULL,
        channel         TEXT    NOT NULL CHECK (channel IN ('me', 'others')),
        text            TEXT    NOT NULL
    )
    """,
    "CREATE INDEX ts_recording_id_idx ON transcript_segments (recording_id, start_ms)",
    # External-content FTS5 index mirroring transcript_segments.text — see triggers below for sync.
    """
    CREATE VIRTUAL TABLE transcript_fts USING fts5(
        text,
        content='transcript_segments',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    )
    """,
    # Triggers keep the FTS index in sync with the source table.
    """
    CREATE TRIGGER ts_ai AFTER INSERT ON transcript_segments BEGIN
        INSERT INTO transcript_fts(rowid, text) VALUES (new.id, new.text);
    END
    """,
    """
    CREATE TRIGGER ts_ad AFTER DELETE ON transcript_segments BEGIN
        INSERT INTO transcript_fts(transcript_fts, rowid, text)
            VALUES ('delete', old.id, old.text);
    END
    """,
    """
    CREATE TRIGGER ts_au AFTER UPDATE ON transcript_segments BEGIN
        INSERT INTO transcript_fts(transcript_fts, rowid, text)
            VALUES ('delete', old.id, old.text);
        INSERT INTO transcript_fts(rowid, text) VALUES (new.id, new.text);
    END
    """,
    """
    CREATE TABLE summaries (
        recording_id            INTEGER PRIMARY KEY
                                REFERENCES recordings(id) ON DELETE CASCADE,
        one_line                TEXT,
        summary                 TEXT,
        key_decisions_json      TEXT NOT NULL DEFAULT '[]',
        my_todos_json           TEXT NOT NULL DEFAULT '[]',
        action_items_others_json TEXT NOT NULL DEFAULT '[]',
        follow_ups_json         TEXT NOT NULL DEFAULT '[]',
        topics_json             TEXT NOT NULL DEFAULT '[]',
        generated_at            TEXT NOT NULL,
        model_used              TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE todo_state (
        id              INTEGER PRIMARY KEY,
        recording_id    INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        todo_index      INTEGER NOT NULL,
        task_text       TEXT    NOT NULL,
        done            INTEGER NOT NULL DEFAULT 0,
        done_at         TEXT,
        UNIQUE (recording_id, todo_index)
    )
    """,
    "CREATE INDEX todo_state_recording_idx ON todo_state (recording_id)",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V1 = Migration(version=1, name="initial schema", apply=_apply)
