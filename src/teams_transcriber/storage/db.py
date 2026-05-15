"""Owns the SQLite connection and exposes a locked context manager."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from teams_transcriber.storage.migrations import Migration, MigrationRunner


class Database:
    """Process-wide SQLite handle with a re-entrant lock.

    Use as:
        db = Database(path, migrations=[...])
        db.initialize()
        with db.connect() as conn:
            conn.execute(...)
            ...
        db.close()
    """

    def __init__(self, path: Path, migrations: Sequence[Migration]) -> None:
        self._path = path
        self._migrations = tuple(migrations)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        """Open connection, set pragmas, run migrations. Safe to call once."""
        with self._lock:
            if self._conn is not None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")  # better concurrency
            conn.execute("PRAGMA synchronous = NORMAL")  # WAL-safe durability/speed tradeoff
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if mode.lower() != "wal":
                raise RuntimeError(
                    f"WAL mode could not be enabled (got {mode!r}). "
                    "This usually indicates the database file is on an unsupported filesystem."
                )
            MigrationRunner(self._migrations).run(conn)
            self._conn = conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield the underlying connection under the write lock.

        SQLite handles concurrent reads internally; we serialize all access for simplicity.
        Returning the connection (not a cursor) lets callers chain multiple statements
        inside a single locked block.
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized: call initialize() first")
        with self._lock:
            yield self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
