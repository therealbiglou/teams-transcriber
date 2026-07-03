"""Verify schema v6 rebuilds wrike_tasks: widens kind CHECK and adds
format/assignee_id columns. Pre-existing rows are preserved with defaults.

Adapted from the Phase 13 plan: the plan's `_build_v5_db` used
`build_database(...)` (which applies ALL migrations including v6), so it could
not simulate a genuine v5-only DB. We instead construct a `Database` with only
the v1..v5 migration set and `initialize()` it. `initialize()` enables
`PRAGMA foreign_keys = ON`, so the subsequent `MigrationRunner([SCHEMA_V6])`
exercises the real fk-OFF-around-rebuild toggle path.
"""

from __future__ import annotations

import sqlite3

import pytest

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage.db import Database
from teams_transcriber.storage.migrations import MigrationRunner
from teams_transcriber.storage.schema_v1 import SCHEMA_V1
from teams_transcriber.storage.schema_v2 import SCHEMA_V2
from teams_transcriber.storage.schema_v3 import SCHEMA_V3
from teams_transcriber.storage.schema_v4 import SCHEMA_V4
from teams_transcriber.storage.schema_v5 import SCHEMA_V5
from teams_transcriber.storage.schema_v6 import SCHEMA_V6

_V1_TO_V5 = (SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5)


def _build_v5_db(tmp_path) -> Database:
    """Apply schemas v1..v5 only (no v6) to simulate an existing user DB."""
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = Database(paths.db_path, migrations=_V1_TO_V5)
    db.initialize()
    return db


def test_v6_migration_preserves_existing_rows_and_adds_columns(tmp_path) -> None:
    db = _build_v5_db(tmp_path)
    # Insert a recording + a v5-shape wrike_tasks row.
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO recordings (started_at, source, detected_title, "
            "status, audio_path) VALUES (?, 'manual', 't', 'done', NULL)",
            ("2026-06-09T10:00:00+00:00",),
        )
        rec_id = conn.execute("SELECT id FROM recordings").fetchone()[0]
        conn.execute(
            "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
            "wrike_task_id, wrike_folder_id, created_at, last_synced_done) "
            "VALUES (?, 'my', 0, 'TASK123', 'FOLDER1', ?, 1)",
            (rec_id, "2026-06-09T10:00:00+00:00"),
        )
        conn.commit()

    # Now apply v6.
    with db.connect() as conn:
        MigrationRunner([SCHEMA_V6]).run(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6

        # Existing row preserved (incl. last_synced_done) with defaults for new columns.
        row = conn.execute(
            "SELECT kind, todo_index, wrike_task_id, last_synced_done, format, assignee_id "
            "FROM wrike_tasks WHERE recording_id=?", (rec_id,),
        ).fetchone()
        assert tuple(row) == ("my", 0, "TASK123", 1, "task", None)

        # Widened CHECK accepts the new kinds.
        for kind in ("summary", "decisions", "follow_up"):
            conn.execute(
                "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
                "wrike_task_id, wrike_folder_id, created_at, last_synced_done, "
                "format, assignee_id) VALUES (?, ?, 0, 'X', 'F', ?, 0, 'comment', NULL)",
                (rec_id, kind, "2026-06-09T10:00:00+00:00"),
            )
        conn.commit()

        # Old narrow values still rejected outside the expanded set.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
                "wrike_task_id, wrike_folder_id, created_at, last_synced_done) "
                "VALUES (?, 'bogus', 9, 'X', 'F', ?, 0)",
                (rec_id, "2026-06-09T10:00:00+00:00"),
            )

        # Format CHECK enforces task/comment only.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
                "wrike_task_id, wrike_folder_id, created_at, last_synced_done, "
                "format, assignee_id) VALUES (?, 'my', 7, 'X', 'F', ?, 0, 'description', NULL)",
                (rec_id, "2026-06-09T10:00:00+00:00"),
            )

        # Index still exists.
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='wrike_tasks_recording_idx'"
        ).fetchone()
        assert idx is not None
    db.close()


def test_v6_cascade_delete_still_works(tmp_path) -> None:
    """Deleting a recording cascades to its wrike_tasks rows after the rebuild."""
    db = _build_v5_db(tmp_path)
    with db.connect() as conn:
        MigrationRunner([SCHEMA_V6]).run(conn)
        conn.execute(
            "INSERT INTO recordings (started_at, source, detected_title, "
            "status, audio_path) VALUES (?, 'manual', 't', 'done', NULL)",
            ("2026-06-09T10:00:00+00:00",),
        )
        rec_id = conn.execute("SELECT id FROM recordings").fetchone()[0]
        conn.execute(
            "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
            "wrike_task_id, wrike_folder_id, created_at, last_synced_done) "
            "VALUES (?, 'my', 0, 'TASK', 'FOLDER', ?, 0)",
            (rec_id, "2026-06-09T10:00:00+00:00"),
        )
        conn.execute("DELETE FROM recordings WHERE id=?", (rec_id,))
        conn.commit()
        remaining = conn.execute(
            "SELECT COUNT(*) FROM wrike_tasks WHERE recording_id=?", (rec_id,),
        ).fetchone()[0]
        assert remaining == 0
    db.close()
