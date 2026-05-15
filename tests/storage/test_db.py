import threading
from pathlib import Path

import pytest

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.migrations import Migration


def _noop_migration() -> Migration:
    return Migration(1, "noop", lambda c: c.execute("CREATE TABLE t (id INTEGER)"))


def test_database_initializes_with_no_migrations(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db", migrations=[])
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    db.close()


def test_database_runs_migrations_on_initialize(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db", migrations=[_noop_migration()])
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='t'"
        ).fetchone() is not None
    db.close()


def test_database_enables_foreign_keys(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db", migrations=[])
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    db.close()


def test_database_uses_row_factory(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db", migrations=[_noop_migration()])
    db.initialize()
    with db.connect() as conn:
        conn.execute("INSERT INTO t (id) VALUES (42)")
        row = conn.execute("SELECT id FROM t").fetchone()
        assert row["id"] == 42  # Row factory exposes column-name access.
    db.close()


def test_database_writes_are_serialized_across_threads(tmp_path: Path) -> None:
    db = Database(
        tmp_path / "t.db",
        migrations=[Migration(1, "counter", lambda c: c.execute("CREATE TABLE c (n INTEGER)"))],
    )
    db.initialize()

    def worker() -> None:
        for _ in range(50):
            with db.connect() as conn:
                conn.execute("INSERT INTO c (n) VALUES (1)")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM c").fetchone()[0]
    db.close()
    assert count == 200


def test_close_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db", migrations=[])
    db.initialize()
    db.close()
    db.close()  # second call must not raise


def test_connect_before_initialize_raises(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db", migrations=[])
    with pytest.raises(RuntimeError, match="initialize"), db.connect():
        pass


def test_database_journal_mode_is_wal(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db", migrations=[])
    db.initialize()
    with db.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    db.close()
    assert mode.lower() == "wal"


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db", migrations=[])
    db.initialize()
    first_conn = db._conn  # access the private attr just for the test
    db.initialize()  # second call must be a no-op
    second_conn = db._conn
    assert first_conn is second_conn
    db.close()
