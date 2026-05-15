import sqlite3

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
