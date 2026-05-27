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
        fk_was_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        for m in self._migrations:
            if m.version <= current:
                continue
            # Table-rebuild migrations (CREATE new / DROP old / RENAME) must run with
            # foreign_keys OFF, otherwise dropping a referenced table cascade-deletes
            # child rows and RENAME rewrites child FK references. The pragma is a no-op
            # inside a transaction, so toggle it OUTSIDE the BEGIN per the SQLite docs
            # ("Making Other Kinds Of Table Schema Changes"). Harmless for pure
            # CREATE/ALTER migrations that touch no parent tables.
            if fk_was_on:
                conn.execute("PRAGMA foreign_keys = OFF")
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
            finally:
                if fk_was_on:
                    conn.execute("PRAGMA foreign_keys = ON")
            current = m.version
