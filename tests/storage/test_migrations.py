import sqlite3
from pathlib import Path

import pytest

from teams_transcriber.storage.migrations import Migration, MigrationRunner


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_runner_applies_pending_migrations_in_order() -> None:
    conn = _make_conn()
    applied: list[int] = []

    def m1(c: sqlite3.Connection) -> None:
        applied.append(1)
        c.execute("CREATE TABLE t1 (id INTEGER)")

    def m2(c: sqlite3.Connection) -> None:
        applied.append(2)
        c.execute("CREATE TABLE t2 (id INTEGER)")

    runner = MigrationRunner([Migration(1, "first", m1), Migration(2, "second", m2)])
    runner.run(conn)

    assert applied == [1, 2]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_runner_skips_already_applied() -> None:
    conn = _make_conn()
    conn.execute("PRAGMA user_version = 1")

    calls: list[int] = []

    def m1(c: sqlite3.Connection) -> None:
        calls.append(1)

    def m2(c: sqlite3.Connection) -> None:
        calls.append(2)
        c.execute("CREATE TABLE t2 (id INTEGER)")

    MigrationRunner([Migration(1, "first", m1), Migration(2, "second", m2)]).run(conn)

    assert calls == [2]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_runner_rolls_back_on_failure() -> None:
    conn = _make_conn()

    def good(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE good (id INTEGER)")

    def bad(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE bad (id INTEGER)")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        MigrationRunner([Migration(1, "good", good), Migration(2, "bad", bad)]).run(conn)

    # v1 should be applied; v2 should be rolled back.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='bad'"
    ).fetchone() is None


def test_runner_requires_strictly_increasing_versions() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        MigrationRunner([
            Migration(2, "two", lambda c: None),
            Migration(1, "one", lambda c: None),
        ])


def test_runner_rejects_version_zero() -> None:
    with pytest.raises(ValueError, match="must be >= 1"):
        MigrationRunner([Migration(0, "zero", lambda c: None)])


def test_runner_rolls_back_partial_ddl_within_a_migration() -> None:
    """Multiple DDLs inside one migration must be atomic — the explicit BEGIN ensures this."""
    conn = _make_conn()

    def bad(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE a (id INTEGER)")
        c.execute("CREATE TABLE b (id INTEGER)")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        MigrationRunner([Migration(1, "bad", bad)]).run(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    leftover = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('a','b')"
    ).fetchall()
    assert leftover == []


def test_schema_v1_creates_expected_objects(tmp_path: Path) -> None:
    from teams_transcriber.storage.db import Database
    from teams_transcriber.storage.schema_v1 import SCHEMA_V1

    db = Database(tmp_path / "t.db", migrations=[SCHEMA_V1])
    db.initialize()
    expected_tables = {
        "recordings",
        "transcript_segments",
        "transcript_fts",
        "summaries",
        "todo_state",
    }
    with db.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()
        }
        assert expected_tables.issubset(names)
        # Triggers must exist for FTS sync.
        triggers = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        assert {"ts_ai", "ts_ad", "ts_au"}.issubset(triggers)
    db.close()


def test_schema_v1_fts_triggers_sync_index(tmp_path: Path) -> None:
    """Verifies the AFTER INSERT/DELETE/UPDATE triggers actually keep transcript_fts in sync."""
    from teams_transcriber.storage.db import Database
    from teams_transcriber.storage.schema_v1 import SCHEMA_V1

    db = Database(tmp_path / "t.db", migrations=[SCHEMA_V1])
    db.initialize()
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO recordings (started_at, source, status) VALUES (?, 'manual', 'recording')",
                ("2026-05-14T10:00:00+00:00",),
            )
            rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # INSERT trigger
            conn.execute(
                "INSERT INTO transcript_segments (recording_id, start_ms, end_ms, channel, text) "
                "VALUES (?, 0, 1000, 'me', 'hello world')",
                (rid,),
            )
            seg_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            hits = conn.execute(
                "SELECT rowid FROM transcript_fts WHERE transcript_fts MATCH 'hello'"
            ).fetchall()
            assert len(hits) == 1, "AFTER INSERT trigger should have populated FTS"

            # UPDATE trigger — old text should no longer match, new text should
            conn.execute(
                "UPDATE transcript_segments SET text = 'goodbye moon' WHERE id = ?",
                (seg_id,),
            )
            assert conn.execute(
                "SELECT COUNT(*) FROM transcript_fts WHERE transcript_fts MATCH 'hello'"
            ).fetchone()[0] == 0, "AFTER UPDATE trigger should have removed the old text"
            assert conn.execute(
                "SELECT COUNT(*) FROM transcript_fts WHERE transcript_fts MATCH 'goodbye'"
            ).fetchone()[0] == 1, "AFTER UPDATE trigger should have inserted the new text"

            # DELETE trigger
            conn.execute("DELETE FROM transcript_segments WHERE id = ?", (seg_id,))
            assert conn.execute(
                "SELECT COUNT(*) FROM transcript_fts WHERE transcript_fts MATCH 'goodbye'"
            ).fetchone()[0] == 0, "AFTER DELETE trigger should have removed the row from FTS"
            conn.commit()
    finally:
        db.close()
