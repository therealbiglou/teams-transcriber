"""Versioned schema migrations driven by SQLite's PRAGMA user_version."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

MigrationFn = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True, slots=True)
class Migration:
    """A single forward-only schema change."""

    version: int  # 1-based target version after this migration runs.
    name: str
    apply: MigrationFn


class MigrationRunner:
    """Applies a sequence of Migrations in version order, wrapped in transactions."""

    def __init__(self, migrations: Sequence[Migration]) -> None:
        for m in migrations:
            if m.version < 1:
                raise ValueError(f"migration {m.name!r}: version must be >= 1, got {m.version}")
        for prev, nxt in pairwise(migrations):
            if nxt.version <= prev.version:
                raise ValueError(
                    "migration versions must be strictly increasing "
                    f"(got {prev.version} then {nxt.version})"
                )
        self._migrations: tuple[Migration, ...] = tuple(migrations)

    def run(self, conn: sqlite3.Connection) -> None:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for m in self._migrations:
            if m.version <= current:
                continue
            # Explicit BEGIN so DDL (CREATE TABLE, etc.) is included in the transaction.
            # Python's sqlite3 only auto-begins for DML by default, leaving DDL unwrapped.
            conn.execute("BEGIN")
            try:
                m.apply(conn)
                conn.execute(f"PRAGMA user_version = {m.version}")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            current = m.version
