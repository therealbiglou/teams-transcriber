# Phase 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Python project, dev tooling, and the storage layer (SQLite schema, migrations, repositories, FTS5) so that subsequent phases can write to and read from a fully tested data layer.

**Architecture:** A single `teams_transcriber` Python package with a `storage` submodule. Storage uses raw SQLite (stdlib `sqlite3`) with one connection per process, a re-entrant lock around mutating calls, and `PRAGMA user_version`-based migrations. Each table has its own repository module (no ORM). Tests use `:memory:` SQLite for unit tests and `tmp_path` fixtures for integration tests.

**Tech Stack:** Python 3.11+, `uv` for env/dep management, `pytest` for tests, `ruff` for lint/format, `mypy` for type checking. SQLite via stdlib (no external DB driver). FTS5 via SQLite's built-in virtual table.

**Spec reference:** [`docs/superpowers/specs/2026-05-14-teams-transcriber-design.md`](../specs/2026-05-14-teams-transcriber-design.md) — Sections 8 (Storage), 11 (Phasing).

---

## File structure (Phase 1 produces)

```
teams-transcriber/
├── pyproject.toml                          NEW — project metadata + dev deps
├── .python-version                         NEW — pins 3.11
├── ruff.toml                               NEW — lint/format config
├── src/
│   └── teams_transcriber/
│       ├── __init__.py                     NEW — version constant
│       ├── paths.py                        NEW — %LOCALAPPDATA% resolution
│       └── storage/
│           ├── __init__.py                 NEW — public API re-exports
│           ├── db.py                       NEW — Database class (connection, init, lock)
│           ├── migrations.py               NEW — versioned migration runner
│           ├── schema_v1.py                NEW — initial schema (recordings, segments, FTS, summaries, todos)
│           ├── models.py                   NEW — Recording, TranscriptSegment, Summary, TodoState dataclasses
│           ├── recordings.py               NEW — Recording CRUD
│           ├── transcripts.py              NEW — TranscriptSegment append/list + FTS search
│           ├── summaries.py                NEW — Summary CRUD
│           ├── todos.py                    NEW — TodoState CRUD
│           └── retention.py                NEW — audio retention pruner
└── tests/
    ├── __init__.py                         NEW
    ├── conftest.py                         NEW — shared fixtures (db, tmp paths)
    └── storage/
        ├── __init__.py                     NEW
        ├── test_paths.py                   NEW
        ├── test_db.py                      NEW
        ├── test_migrations.py              NEW
        ├── test_recordings.py              NEW
        ├── test_transcripts.py             NEW (includes FTS search tests)
        ├── test_summaries.py               NEW
        ├── test_todos.py                   NEW
        ├── test_retention.py               NEW
        └── test_lifecycle.py               NEW (integration: full lifecycle smoke)
```

---

## Task 1: Project bootstrap (uv, pyproject, dev tooling)

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `ruff.toml`
- Create: `src/teams_transcriber/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/storage/__init__.py`

This task has no failing test — it's project setup. We finish by running `pytest --collect-only` and confirming tests are discoverable.

- [ ] **Step 1: Install `uv` if not present**

Run in PowerShell:
```powershell
uv --version
```

Expected: prints a version, e.g. `uv 0.4.x`. If `uv` is not found, install via:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
Then re-run `uv --version`.

- [ ] **Step 2: Create `.python-version`**

```
3.11
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "teams-transcriber"
version = "0.1.0"
description = "Auto-record, transcribe, and summarize Microsoft Teams meetings on Windows."
readme = "README.md"
requires-python = ">=3.11"
authors = [{ name = "Brian Lewis" }]

# Phase 1 has zero runtime deps — stdlib only.
dependencies = []

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=4.1",
    "ruff>=0.5",
    "mypy>=1.10",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/teams_transcriber"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -q --strict-markers"
filterwarnings = ["error"]

[tool.mypy]
python_version = "3.11"
strict = true
files = ["src/teams_transcriber"]
```

- [ ] **Step 4: Create `ruff.toml`**

```toml
target-version = "py311"
line-length = 100

[lint]
select = [
    "E", "F", "W",   # pycodestyle / pyflakes
    "I",             # isort
    "B",             # bugbear
    "UP",            # pyupgrade
    "SIM",           # simplify
    "RUF",           # ruff-specific
]
ignore = ["E501"]  # line length handled by formatter

[format]
quote-style = "double"
```

- [ ] **Step 5: Create `src/teams_transcriber/__init__.py`**

```python
"""Teams Transcriber — auto-record and summarize Teams meetings."""

__version__ = "0.1.0"
```

- [ ] **Step 6: Create empty `tests/__init__.py` and `tests/storage/__init__.py`**

Both files have empty content (just create them).

- [ ] **Step 7: Sync dev environment**

Run:
```powershell
uv sync --extra dev
```

Expected: a `.venv` is created and dev deps are installed.

- [ ] **Step 8: Verify pytest can collect**

Run:
```powershell
uv run pytest --collect-only
```

Expected: `collected 0 items` (no tests yet, but pytest discovers).

- [ ] **Step 9: Verify ruff and mypy run**

Run:
```powershell
uv run ruff check src tests
uv run mypy
```

Expected: both report success (or "no files to check" for mypy if it doesn't find files).

- [ ] **Step 10: Commit**

```powershell
git add pyproject.toml .python-version ruff.toml src tests
git commit -m "chore: bootstrap Python project with uv, pytest, ruff, mypy"
```

---

## Task 2: Paths helper

**Files:**
- Create: `src/teams_transcriber/paths.py`
- Test: `tests/storage/test_paths.py`

A small module that resolves the standard app-data paths described in spec Section 8.1. Centralized here so every other module looks up paths from one place.

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_paths.py`:
```python
from pathlib import Path

import pytest

from teams_transcriber.paths import AppPaths


def test_paths_defaults_to_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    paths = AppPaths()
    assert paths.root == tmp_path / "TeamsTranscriber"
    assert paths.db_path == tmp_path / "TeamsTranscriber" / "teams_transcriber.db"
    assert paths.audio_dir == tmp_path / "TeamsTranscriber" / "audio"
    assert paths.models_dir == tmp_path / "TeamsTranscriber" / "models"
    assert paths.logs_dir == tmp_path / "TeamsTranscriber" / "logs"
    assert paths.config_dir == tmp_path / "TeamsTranscriber" / "config"


def test_paths_accepts_explicit_root(tmp_path: Path) -> None:
    custom = tmp_path / "custom_root"
    paths = AppPaths(root=custom)
    assert paths.root == custom
    assert paths.db_path == custom / "teams_transcriber.db"


def test_ensure_dirs_creates_all_directories(tmp_path: Path) -> None:
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    assert paths.root.is_dir()
    assert paths.audio_dir.is_dir()
    assert paths.models_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.config_dir.is_dir()


def test_ensure_dirs_is_idempotent(tmp_path: Path) -> None:
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    paths.ensure_dirs()  # second call must not raise
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
uv run pytest tests/storage/test_paths.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'teams_transcriber.paths'`.

- [ ] **Step 3: Implement `paths.py`**

Create `src/teams_transcriber/paths.py`:
```python
"""Resolves on-disk paths for app data, audio, models, logs, and config."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR_NAME = "TeamsTranscriber"


def _default_root() -> Path:
    """Return %LOCALAPPDATA%\\TeamsTranscriber, with sensible fallbacks for non-Windows test envs."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    # Fallback: ~/.local/share/TeamsTranscriber (developer machines, CI).
    return Path.home() / ".local" / "share" / APP_DIR_NAME


@dataclass(slots=True)
class AppPaths:
    """Standard locations for app-managed files. Override `root` for tests."""

    root: Path = field(default_factory=_default_root)

    @property
    def db_path(self) -> Path:
        return self.root / "teams_transcriber.db"

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    def ensure_dirs(self) -> None:
        """Create all managed directories. Safe to call repeatedly."""
        for d in (self.root, self.audio_dir, self.models_dir, self.logs_dir, self.config_dir):
            d.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

```powershell
uv run pytest tests/storage/test_paths.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Lint & type-check**

```powershell
uv run ruff check src tests
uv run mypy
```
Expected: both clean.

- [ ] **Step 6: Commit**

```powershell
git add src/teams_transcriber/paths.py tests/storage/test_paths.py
git commit -m "feat(storage): add AppPaths for app-data path resolution"
```

---

## Task 3: Database connection class + migration framework

**Files:**
- Create: `src/teams_transcriber/storage/__init__.py`
- Create: `src/teams_transcriber/storage/db.py`
- Create: `src/teams_transcriber/storage/migrations.py`
- Create: `tests/conftest.py`
- Test: `tests/storage/test_db.py`
- Test: `tests/storage/test_migrations.py`

A `Database` class owning one `sqlite3.Connection` (created with `check_same_thread=False`) plus a `threading.RLock`. A `MigrationRunner` reads `PRAGMA user_version`, applies any pending migrations in order, and bumps the version.

- [ ] **Step 1: Create empty `storage/__init__.py`**

```python
"""Storage layer: SQLite-backed persistence for recordings, transcripts, and summaries."""
```

- [ ] **Step 2: Write the failing tests for migrations**

Create `tests/storage/test_migrations.py`:
```python
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
```

- [ ] **Step 3: Run migration tests to verify they fail**

```powershell
uv run pytest tests/storage/test_migrations.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement `migrations.py`**

Create `src/teams_transcriber/storage/migrations.py`:
```python
"""Versioned schema migrations driven by SQLite's PRAGMA user_version."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

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
        for prev, nxt in zip(migrations, migrations[1:], strict=False):
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
            # SQLite implicit transaction: we open, do work, then either commit or rollback.
            try:
                m.apply(conn)
                conn.execute(f"PRAGMA user_version = {m.version}")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            current = m.version
```

- [ ] **Step 5: Run migration tests to verify they pass**

```powershell
uv run pytest tests/storage/test_migrations.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 6: Write the failing tests for `Database`**

Create `tests/storage/test_db.py`:
```python
import sqlite3
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
    with pytest.raises(RuntimeError, match="initialize"):
        with db.connect():
            pass
```

- [ ] **Step 7: Run `Database` tests to verify they fail**

```powershell
uv run pytest tests/storage/test_db.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 8: Implement `db.py`**

Create `src/teams_transcriber/storage/db.py`:
```python
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
        if self._conn is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")  # better concurrency
        conn.execute("PRAGMA synchronous = NORMAL")  # WAL-safe durability/speed tradeoff
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
```

- [ ] **Step 9: Run `Database` tests to verify they pass**

```powershell
uv run pytest tests/storage/test_db.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 10: Add shared test fixtures**

Create `tests/conftest.py`:
```python
"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.schema_v1 import SCHEMA_V1


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    """An initialized Database with the v1 schema applied. Cleaned up after the test."""
    database = Database(tmp_path / "test.db", migrations=[SCHEMA_V1])
    database.initialize()
    try:
        yield database
    finally:
        database.close()
```

(Note: this fixture imports `SCHEMA_V1` from a module we create in Task 4. Tests that use `db` will fail until then — that's fine; we add Task 4 next.)

- [ ] **Step 11: Lint & type-check**

```powershell
uv run ruff check src tests
uv run mypy
```
Expected: both clean.

- [ ] **Step 12: Commit**

```powershell
git add src/teams_transcriber/storage tests/conftest.py tests/storage/test_db.py tests/storage/test_migrations.py
git commit -m "feat(storage): add Database connection class and migration framework"
```

---

## Task 4: Initial schema migration (v1)

**Files:**
- Create: `src/teams_transcriber/storage/schema_v1.py`
- Test: (covered indirectly via fixture in subsequent tasks; this task adds a minimal direct test)

Defines `SCHEMA_V1`, the first `Migration` object that creates all tables, the FTS5 virtual table, and the triggers that keep FTS in sync with `transcript_segments`.

- [ ] **Step 1: Write a direct test that the v1 migration creates everything**

Append to `tests/storage/test_migrations.py` (at the bottom):
```python


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
```

And add the import at the top of `test_migrations.py`:
```python
from pathlib import Path
```

- [ ] **Step 2: Run the new test to verify it fails**

```powershell
uv run pytest tests/storage/test_migrations.py::test_schema_v1_creates_expected_objects -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'teams_transcriber.storage.schema_v1'`.

- [ ] **Step 3: Implement `schema_v1.py`**

Create `src/teams_transcriber/storage/schema_v1.py`:
```python
"""Initial schema (v1): recordings, transcript_segments, transcript_fts, summaries, todo_state."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration


def _apply(conn: sqlite3.Connection) -> None:
    conn.executescript(
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
        );

        CREATE INDEX recordings_started_at_idx ON recordings (started_at DESC);
        CREATE INDEX recordings_status_idx     ON recordings (status);

        CREATE TABLE transcript_segments (
            id              INTEGER PRIMARY KEY,
            recording_id    INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
            start_ms        INTEGER NOT NULL,
            end_ms          INTEGER NOT NULL,
            channel         TEXT    NOT NULL CHECK (channel IN ('me', 'others')),
            text            TEXT    NOT NULL
        );

        CREATE INDEX ts_recording_id_idx ON transcript_segments (recording_id, start_ms);

        -- Contentless FTS5 index over transcript_segments.text.
        CREATE VIRTUAL TABLE transcript_fts USING fts5(
            text,
            content='transcript_segments',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );

        -- Triggers keep FTS in sync with the base table.
        CREATE TRIGGER ts_ai AFTER INSERT ON transcript_segments BEGIN
            INSERT INTO transcript_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER ts_ad AFTER DELETE ON transcript_segments BEGIN
            INSERT INTO transcript_fts(transcript_fts, rowid, text)
                VALUES ('delete', old.id, old.text);
        END;
        CREATE TRIGGER ts_au AFTER UPDATE ON transcript_segments BEGIN
            INSERT INTO transcript_fts(transcript_fts, rowid, text)
                VALUES ('delete', old.id, old.text);
            INSERT INTO transcript_fts(rowid, text) VALUES (new.id, new.text);
        END;

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
        );

        CREATE TABLE todo_state (
            id              INTEGER PRIMARY KEY,
            recording_id    INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
            todo_index      INTEGER NOT NULL,
            task_text       TEXT    NOT NULL,
            done            INTEGER NOT NULL DEFAULT 0,
            done_at         TEXT,
            UNIQUE (recording_id, todo_index)
        );

        CREATE INDEX todo_state_recording_idx ON todo_state (recording_id);
        """
    )


SCHEMA_V1 = Migration(version=1, name="initial schema", apply=_apply)
```

- [ ] **Step 4: Run the test to verify it passes**

```powershell
uv run pytest tests/storage/test_migrations.py::test_schema_v1_creates_expected_objects -v
```
Expected: PASS.

- [ ] **Step 5: Run the full test suite (the `db` fixture now resolves)**

```powershell
uv run pytest -v
```
Expected: all tests PASS.

- [ ] **Step 6: Lint & type-check**

```powershell
uv run ruff check src tests
uv run mypy
```
Expected: both clean.

- [ ] **Step 7: Commit**

```powershell
git add src/teams_transcriber/storage/schema_v1.py tests/storage/test_migrations.py
git commit -m "feat(storage): add v1 schema (recordings, transcripts, summaries, todos, FTS)"
```

---

## Task 5: Recording model + repository

**Files:**
- Create: `src/teams_transcriber/storage/models.py` (Recording dataclass added here; other dataclasses added in later tasks)
- Create: `src/teams_transcriber/storage/recordings.py`
- Test: `tests/storage/test_recordings.py`

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_recordings.py`:
```python
from datetime import UTC, datetime

import pytest

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import Recording, RecordingStatus, RecordingSource
from teams_transcriber.storage.recordings import RecordingRepo


def _now() -> str:
    return datetime.now(UTC).isoformat()


def test_create_returns_recording_with_id(db: Database) -> None:
    repo = RecordingRepo(db)
    rec = repo.create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="Meeting | Microsoft Teams",
            display_title=None,
            audio_path="C:/tmp/a.opus",
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    assert rec.id is not None
    assert rec.detected_title == "Meeting | Microsoft Teams"


def test_get_returns_none_for_missing(db: Database) -> None:
    repo = RecordingRepo(db)
    assert repo.get(999) is None


def test_get_returns_existing(db: Database) -> None:
    repo = RecordingRepo(db)
    created = repo.create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.MANUAL,
            detected_title=None,
            display_title="Manual",
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    fetched = repo.get(created.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.source == RecordingSource.MANUAL
    assert fetched.display_title == "Manual"


def test_update_status_and_error(db: Database) -> None:
    repo = RecordingRepo(db)
    created = repo.create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title=None,
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    repo.update_status(created.id, RecordingStatus.SUMMARY_FAILED, error_message="api down")  # type: ignore[arg-type]
    again = repo.get(created.id)  # type: ignore[arg-type]
    assert again is not None
    assert again.status == RecordingStatus.SUMMARY_FAILED
    assert again.error_message == "api down"


def test_finalize_sets_ended_and_duration(db: Database) -> None:
    repo = RecordingRepo(db)
    created = repo.create(
        Recording(
            id=None,
            started_at="2026-05-14T10:00:00+00:00",
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title=None,
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    repo.finalize(
        created.id,  # type: ignore[arg-type]
        ended_at="2026-05-14T10:05:00+00:00",
        duration_ms=300_000,
    )
    again = repo.get(created.id)  # type: ignore[arg-type]
    assert again is not None
    assert again.ended_at == "2026-05-14T10:05:00+00:00"
    assert again.duration_ms == 300_000


def test_set_display_title(db: Database) -> None:
    repo = RecordingRepo(db)
    created = repo.create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="Meeting | Microsoft Teams",
            display_title=None,
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    repo.set_display_title(created.id, "Q2 roadmap sync")  # type: ignore[arg-type]
    again = repo.get(created.id)  # type: ignore[arg-type]
    assert again is not None
    assert again.display_title == "Q2 roadmap sync"


def test_list_recent_orders_by_started_desc(db: Database) -> None:
    repo = RecordingRepo(db)
    for i in range(3):
        repo.create(
            Recording(
                id=None,
                started_at=f"2026-05-1{i}T10:00:00+00:00",
                ended_at=None,
                source=RecordingSource.TEAMS,
                detected_title=f"Meeting {i}",
                display_title=None,
                audio_path=None,
                audio_deleted_at=None,
                duration_ms=None,
                status=RecordingStatus.DONE,
                error_message=None,
            )
        )
    recents = repo.list_recent(limit=10)
    assert [r.detected_title for r in recents] == ["Meeting 2", "Meeting 1", "Meeting 0"]


def test_delete_cascades(db: Database) -> None:
    repo = RecordingRepo(db)
    created = repo.create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title=None,
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    repo.delete(created.id)  # type: ignore[arg-type]
    assert repo.get(created.id) is None  # type: ignore[arg-type]


def test_create_rejects_invalid_source(db: Database) -> None:
    import sqlite3
    repo = RecordingRepo(db)  # noqa: F841 — repo unused; we exercise the CHECK directly
    with pytest.raises(sqlite3.IntegrityError):
        # Sneak around the enum to exercise the CHECK constraint at the SQL level.
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO recordings (started_at, source, status) VALUES (?, ?, ?)",
                (_now(), "invalid", "recording"),
            )
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
uv run pytest tests/storage/test_recordings.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `models.py` (Recording-related types)**

Create `src/teams_transcriber/storage/models.py`:
```python
"""Dataclasses and enums representing storage rows.

Dataclasses are used (not Pydantic) because storage rows are tiny and we don't want
runtime validation overhead. Validation lives at app boundaries, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecordingSource(StrEnum):
    TEAMS = "teams"
    MANUAL = "manual"


class RecordingStatus(StrEnum):
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    DONE = "done"
    RECORDING_FAILED = "recording_failed"
    TRANSCRIPTION_FAILED = "transcription_failed"
    SUMMARY_FAILED = "summary_failed"


class Channel(StrEnum):
    ME = "me"
    OTHERS = "others"


@dataclass(slots=True)
class Recording:
    id: int | None
    started_at: str  # ISO 8601 UTC
    ended_at: str | None
    source: RecordingSource
    detected_title: str | None
    display_title: str | None
    audio_path: str | None
    audio_deleted_at: str | None
    duration_ms: int | None
    status: RecordingStatus
    error_message: str | None
```

- [ ] **Step 4: Implement `recordings.py`**

Create `src/teams_transcriber/storage/recordings.py`:
```python
"""Repository for the `recordings` table."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import Recording, RecordingSource, RecordingStatus


def _row_to_recording(row: sqlite3.Row) -> Recording:
    return Recording(
        id=row["id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        source=RecordingSource(row["source"]),
        detected_title=row["detected_title"],
        display_title=row["display_title"],
        audio_path=row["audio_path"],
        audio_deleted_at=row["audio_deleted_at"],
        duration_ms=row["duration_ms"],
        status=RecordingStatus(row["status"]),
        error_message=row["error_message"],
    )


class RecordingRepo:
    """CRUD for recordings. All methods serialize on the Database lock."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, rec: Recording) -> Recording:
        with self._db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO recordings (
                    started_at, ended_at, source, detected_title, display_title,
                    audio_path, audio_deleted_at, duration_ms, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.started_at,
                    rec.ended_at,
                    rec.source.value,
                    rec.detected_title,
                    rec.display_title,
                    rec.audio_path,
                    rec.audio_deleted_at,
                    rec.duration_ms,
                    rec.status.value,
                    rec.error_message,
                ),
            )
            conn.commit()
            rec.id = cur.lastrowid
            return rec

    def get(self, recording_id: int) -> Recording | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
        return _row_to_recording(row) if row is not None else None

    def list_recent(self, limit: int = 50) -> list[Recording]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recordings ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_recording(r) for r in rows]

    def update_status(
        self,
        recording_id: int,
        status: RecordingStatus,
        error_message: str | None = None,
    ) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET status = ?, error_message = ? WHERE id = ?",
                (status.value, error_message, recording_id),
            )
            conn.commit()

    def finalize(self, recording_id: int, ended_at: str, duration_ms: int) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET ended_at = ?, duration_ms = ? WHERE id = ?",
                (ended_at, duration_ms, recording_id),
            )
            conn.commit()

    def set_display_title(self, recording_id: int, title: str) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET display_title = ? WHERE id = ?",
                (title, recording_id),
            )
            conn.commit()

    def set_audio_path(self, recording_id: int, audio_path: str | None) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET audio_path = ? WHERE id = ?",
                (audio_path, recording_id),
            )
            conn.commit()

    def mark_audio_deleted(self, recording_id: int, deleted_at: str) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET audio_path = NULL, audio_deleted_at = ? WHERE id = ?",
                (deleted_at, recording_id),
            )
            conn.commit()

    def delete(self, recording_id: int) -> None:
        with self._db.connect() as conn:
            conn.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
            conn.commit()
```

- [ ] **Step 5: Run the test to verify it passes**

```powershell
uv run pytest tests/storage/test_recordings.py -v
```
Expected: all 9 tests PASS.

- [ ] **Step 6: Lint & type-check**

```powershell
uv run ruff check src tests
uv run mypy
```
Expected: both clean.

- [ ] **Step 7: Commit**

```powershell
git add src/teams_transcriber/storage/models.py src/teams_transcriber/storage/recordings.py tests/storage/test_recordings.py
git commit -m "feat(storage): add Recording model and RecordingRepo"
```

---

## Task 6: TranscriptSegment model + repository (with FTS search)

**Files:**
- Modify: `src/teams_transcriber/storage/models.py` (add `TranscriptSegment`)
- Create: `src/teams_transcriber/storage/transcripts.py`
- Test: `tests/storage/test_transcripts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_transcripts.py`:
```python
import pytest

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import (
    Channel,
    Recording,
    RecordingSource,
    RecordingStatus,
    TranscriptSegment,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.transcripts import SearchHit, TranscriptRepo


@pytest.fixture
def recording_id(db: Database) -> int:
    rec = RecordingRepo(db).create(
        Recording(
            id=None,
            started_at="2026-05-14T10:00:00+00:00",
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title="X",
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.TRANSCRIBING,
            error_message=None,
        )
    )
    assert rec.id is not None
    return rec.id


def test_append_and_list_segments(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append(
        TranscriptSegment(
            id=None,
            recording_id=recording_id,
            start_ms=0,
            end_ms=2000,
            channel=Channel.ME,
            text="Hello there",
        )
    )
    repo.append(
        TranscriptSegment(
            id=None,
            recording_id=recording_id,
            start_ms=2000,
            end_ms=4500,
            channel=Channel.OTHERS,
            text="Hi back",
        )
    )
    segs = repo.list_for_recording(recording_id)
    assert [s.text for s in segs] == ["Hello there", "Hi back"]
    assert [s.channel for s in segs] == [Channel.ME, Channel.OTHERS]


def test_append_many_preserves_order(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append_many(
        [
            TranscriptSegment(None, recording_id, 0, 1000, Channel.ME, "one"),
            TranscriptSegment(None, recording_id, 1000, 2000, Channel.ME, "two"),
            TranscriptSegment(None, recording_id, 2000, 3000, Channel.ME, "three"),
        ]
    )
    segs = repo.list_for_recording(recording_id)
    assert [s.text for s in segs] == ["one", "two", "three"]


def test_fts_search_returns_hits(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append_many(
        [
            TranscriptSegment(None, recording_id, 0, 1000, Channel.OTHERS,
                              "Let's discuss the billing rewrite next quarter"),
            TranscriptSegment(None, recording_id, 1000, 2000, Channel.ME,
                              "Sounds good. I'll write the API stub."),
            TranscriptSegment(None, recording_id, 2000, 3000, Channel.OTHERS,
                              "Great, and I'll handle the migration doc."),
        ]
    )
    hits = repo.search("billing")
    assert len(hits) == 1
    assert hits[0].recording_id == recording_id
    assert "billing" in hits[0].snippet.lower()


def test_fts_search_multiword(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append(
        TranscriptSegment(None, recording_id, 0, 1000, Channel.OTHERS,
                          "Schedule the billing rewrite for July")
    )
    hits = repo.search("billing rewrite")
    assert len(hits) == 1


def test_fts_search_returns_empty_when_no_match(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append(
        TranscriptSegment(None, recording_id, 0, 1000, Channel.OTHERS, "Hello world")
    )
    assert repo.search("nonexistent") == []


def test_fts_updates_when_segment_deleted(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    seg = TranscriptSegment(None, recording_id, 0, 1000, Channel.OTHERS, "billing")
    repo.append(seg)
    assert seg.id is not None

    # Delete via SQL (no repo method needed; cascading delete via Recording delete is tested elsewhere).
    with db.connect() as conn:
        conn.execute("DELETE FROM transcript_segments WHERE id = ?", (seg.id,))
        conn.commit()
    assert repo.search("billing") == []


def test_search_handles_special_characters_safely(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append(
        TranscriptSegment(None, recording_id, 0, 1000, Channel.OTHERS, "Hello world")
    )
    # An FTS-significant character used naively would crash; the repo must escape it.
    hits = repo.search('"hello"')
    # The result is allowed to be 0 or 1 — the contract is "doesn't raise".
    assert isinstance(hits, list)


def test_search_includes_recording_title(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append(
        TranscriptSegment(None, recording_id, 0, 1000, Channel.ME, "Quarterly planning chat")
    )
    hits = repo.search("planning")
    assert len(hits) == 1
    assert hits[0].recording_title == "X"  # display_title from fixture
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
uv run pytest tests/storage/test_transcripts.py -v
```
Expected: FAIL — `ImportError: cannot import name 'TranscriptSegment'`.

- [ ] **Step 3: Extend `models.py` with `TranscriptSegment`**

Append to `src/teams_transcriber/storage/models.py`:
```python


@dataclass(slots=True)
class TranscriptSegment:
    id: int | None
    recording_id: int
    start_ms: int
    end_ms: int
    channel: Channel
    text: str
```

- [ ] **Step 4: Implement `transcripts.py`**

Create `src/teams_transcriber/storage/transcripts.py`:
```python
"""Repository for transcript segments + FTS5 search across transcripts."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import Channel, TranscriptSegment


@dataclass(slots=True)
class SearchHit:
    """A single FTS hit. `snippet` is HTML-free, with matched terms wrapped in `<mark>...</mark>`."""

    recording_id: int
    recording_title: str | None
    segment_id: int
    start_ms: int
    end_ms: int
    channel: Channel
    snippet: str


def _row_to_segment(row: sqlite3.Row) -> TranscriptSegment:
    return TranscriptSegment(
        id=row["id"],
        recording_id=row["recording_id"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        channel=Channel(row["channel"]),
        text=row["text"],
    )


def _escape_fts_query(query: str) -> str:
    """Wrap each whitespace-separated token in quotes so FTS treats them as literals.

    FTS5 query syntax interprets characters like `"`, `*`, `:`, `-`, `(`, `)` specially.
    For a search UI we want all user input to be treated as plain text. We escape any
    internal `"` by doubling it, then wrap each token in double quotes.
    """
    tokens = query.split()
    if not tokens:
        return ""
    quoted = []
    for tok in tokens:
        escaped = tok.replace('"', '""')
        quoted.append(f'"{escaped}"')
    return " ".join(quoted)


class TranscriptRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def append(self, seg: TranscriptSegment) -> TranscriptSegment:
        with self._db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO transcript_segments
                    (recording_id, start_ms, end_ms, channel, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (seg.recording_id, seg.start_ms, seg.end_ms, seg.channel.value, seg.text),
            )
            conn.commit()
            seg.id = cur.lastrowid
            return seg

    def append_many(self, segs: Iterable[TranscriptSegment]) -> None:
        rows = [
            (s.recording_id, s.start_ms, s.end_ms, s.channel.value, s.text) for s in segs
        ]
        if not rows:
            return
        with self._db.connect() as conn:
            conn.executemany(
                """
                INSERT INTO transcript_segments
                    (recording_id, start_ms, end_ms, channel, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def list_for_recording(self, recording_id: int) -> list[TranscriptSegment]:
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM transcript_segments
                WHERE recording_id = ?
                ORDER BY start_ms ASC
                """,
                (recording_id,),
            ).fetchall()
        return [_row_to_segment(r) for r in rows]

    def search(self, query: str, limit: int = 50) -> list[SearchHit]:
        """Full-text search over transcript segments. Returns highlighted snippets."""
        match = _escape_fts_query(query)
        if not match:
            return []
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    ts.id              AS segment_id,
                    ts.recording_id    AS recording_id,
                    ts.start_ms        AS start_ms,
                    ts.end_ms          AS end_ms,
                    ts.channel         AS channel,
                    r.display_title    AS recording_title,
                    snippet(transcript_fts, 0, '<mark>', '</mark>', '…', 16) AS snippet
                FROM transcript_fts
                JOIN transcript_segments ts ON ts.id = transcript_fts.rowid
                JOIN recordings           r ON r.id  = ts.recording_id
                WHERE transcript_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        return [
            SearchHit(
                recording_id=row["recording_id"],
                recording_title=row["recording_title"],
                segment_id=row["segment_id"],
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
                channel=Channel(row["channel"]),
                snippet=row["snippet"],
            )
            for row in rows
        ]
```

- [ ] **Step 5: Run the test to verify it passes**

```powershell
uv run pytest tests/storage/test_transcripts.py -v
```
Expected: all 8 tests PASS.

- [ ] **Step 6: Lint & type-check**

```powershell
uv run ruff check src tests
uv run mypy
```
Expected: both clean.

- [ ] **Step 7: Commit**

```powershell
git add src/teams_transcriber/storage/models.py src/teams_transcriber/storage/transcripts.py tests/storage/test_transcripts.py
git commit -m "feat(storage): add TranscriptSegment repo with FTS5 search"
```

---

## Task 7: Summary model + repository

**Files:**
- Modify: `src/teams_transcriber/storage/models.py` (add `Summary`, `TodoItem`, `ActionItemOther`)
- Create: `src/teams_transcriber/storage/summaries.py`
- Test: `tests/storage/test_summaries.py`

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_summaries.py`:
```python
from datetime import UTC, datetime

import pytest

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import (
    ActionItemOther,
    Recording,
    RecordingSource,
    RecordingStatus,
    Summary,
    TodoItem,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.summaries import SummaryRepo


def _now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def recording_id(db: Database) -> int:
    rec = RecordingRepo(db).create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title="X",
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.SUMMARIZING,
            error_message=None,
        )
    )
    assert rec.id is not None
    return rec.id


def _sample_summary(recording_id: int) -> Summary:
    return Summary(
        recording_id=recording_id,
        one_line="Aligned on billing rewrite.",
        summary="We discussed the billing rewrite and agreed on a July release.",
        key_decisions=["Billing rewrite scheduled for July release"],
        my_todos=[
            TodoItem(task="Write API stub spec", context="Discussed at ~12:30", due="2026-05-16"),
        ],
        action_items_others=[
            ActionItemOther(who="Sarah", task="Review billing migration doc", due=None),
        ],
        follow_ups=["Revisit pricing tiers after legal review"],
        topics=["billing", "roadmap"],
        generated_at=_now(),
        model_used="claude-sonnet-4-6",
    )


def test_upsert_creates_summary(db: Database, recording_id: int) -> None:
    repo = SummaryRepo(db)
    repo.upsert(_sample_summary(recording_id))
    got = repo.get(recording_id)
    assert got is not None
    assert got.one_line == "Aligned on billing rewrite."
    assert got.my_todos[0].task == "Write API stub spec"
    assert got.action_items_others[0].who == "Sarah"
    assert got.topics == ["billing", "roadmap"]


def test_upsert_replaces_existing(db: Database, recording_id: int) -> None:
    repo = SummaryRepo(db)
    first = _sample_summary(recording_id)
    repo.upsert(first)

    second = _sample_summary(recording_id)
    second.one_line = "Re-summarized."
    second.topics = ["billing"]
    repo.upsert(second)

    got = repo.get(recording_id)
    assert got is not None
    assert got.one_line == "Re-summarized."
    assert got.topics == ["billing"]


def test_get_returns_none_when_missing(db: Database) -> None:
    repo = SummaryRepo(db)
    assert repo.get(999) is None


def test_empty_lists_round_trip(db: Database, recording_id: int) -> None:
    repo = SummaryRepo(db)
    s = _sample_summary(recording_id)
    s.key_decisions = []
    s.my_todos = []
    s.action_items_others = []
    s.follow_ups = []
    s.topics = []
    repo.upsert(s)
    got = repo.get(recording_id)
    assert got is not None
    assert got.key_decisions == []
    assert got.my_todos == []
    assert got.topics == []
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
uv run pytest tests/storage/test_summaries.py -v
```
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Extend `models.py` with Summary-related types**

Append to `src/teams_transcriber/storage/models.py`:
```python


@dataclass(slots=True)
class TodoItem:
    task: str
    context: str | None = None
    due: str | None = None  # ISO date string or None


@dataclass(slots=True)
class ActionItemOther:
    who: str
    task: str
    due: str | None = None


@dataclass(slots=True)
class Summary:
    recording_id: int
    one_line: str | None
    summary: str | None
    key_decisions: list[str]
    my_todos: list[TodoItem]
    action_items_others: list[ActionItemOther]
    follow_ups: list[str]
    topics: list[str]
    generated_at: str
    model_used: str
```

- [ ] **Step 4: Implement `summaries.py`**

Create `src/teams_transcriber/storage/summaries.py`:
```python
"""Repository for AI-generated summaries (one per recording).

JSON fields are stored as TEXT and (de)serialized at the boundary so the DB schema
stays simple. The Summary dataclass is the canonical in-memory shape.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import (
    ActionItemOther,
    Summary,
    TodoItem,
)


def _row_to_summary(row: sqlite3.Row) -> Summary:
    return Summary(
        recording_id=row["recording_id"],
        one_line=row["one_line"],
        summary=row["summary"],
        key_decisions=json.loads(row["key_decisions_json"]),
        my_todos=[TodoItem(**d) for d in json.loads(row["my_todos_json"])],
        action_items_others=[
            ActionItemOther(**d) for d in json.loads(row["action_items_others_json"])
        ],
        follow_ups=json.loads(row["follow_ups_json"]),
        topics=json.loads(row["topics_json"]),
        generated_at=row["generated_at"],
        model_used=row["model_used"],
    )


class SummaryRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, summary: Summary) -> None:
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO summaries (
                    recording_id, one_line, summary,
                    key_decisions_json, my_todos_json, action_items_others_json,
                    follow_ups_json, topics_json,
                    generated_at, model_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recording_id) DO UPDATE SET
                    one_line = excluded.one_line,
                    summary = excluded.summary,
                    key_decisions_json = excluded.key_decisions_json,
                    my_todos_json = excluded.my_todos_json,
                    action_items_others_json = excluded.action_items_others_json,
                    follow_ups_json = excluded.follow_ups_json,
                    topics_json = excluded.topics_json,
                    generated_at = excluded.generated_at,
                    model_used = excluded.model_used
                """,
                (
                    summary.recording_id,
                    summary.one_line,
                    summary.summary,
                    json.dumps(summary.key_decisions),
                    json.dumps([asdict(t) for t in summary.my_todos]),
                    json.dumps([asdict(a) for a in summary.action_items_others]),
                    json.dumps(summary.follow_ups),
                    json.dumps(summary.topics),
                    summary.generated_at,
                    summary.model_used,
                ),
            )
            conn.commit()

    def get(self, recording_id: int) -> Summary | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM summaries WHERE recording_id = ?", (recording_id,)
            ).fetchone()
        return _row_to_summary(row) if row is not None else None
```

- [ ] **Step 5: Run the test to verify it passes**

```powershell
uv run pytest tests/storage/test_summaries.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 6: Lint & type-check**

```powershell
uv run ruff check src tests
uv run mypy
```
Expected: both clean.

- [ ] **Step 7: Commit**

```powershell
git add src/teams_transcriber/storage/models.py src/teams_transcriber/storage/summaries.py tests/storage/test_summaries.py
git commit -m "feat(storage): add Summary model and SummaryRepo (upsert + get)"
```

---

## Task 8: TodoState model + repository

**Files:**
- Modify: `src/teams_transcriber/storage/models.py` (add `TodoState`)
- Create: `src/teams_transcriber/storage/todos.py`
- Test: `tests/storage/test_todos.py`

Tracks the checkbox state for each item in a Summary's `my_todos`. Survives re-summarization via the `(recording_id, todo_index, task_text)` composite key — if the model re-summarizes and produces the same `task_text` at the same `todo_index`, we keep its done state.

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_todos.py`:
```python
from datetime import UTC, datetime

import pytest

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import (
    Recording,
    RecordingSource,
    RecordingStatus,
    TodoState,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.todos import TodoStateRepo


def _now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def recording_id(db: Database) -> int:
    rec = RecordingRepo(db).create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title="X",
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.DONE,
            error_message=None,
        )
    )
    assert rec.id is not None
    return rec.id


def test_upsert_inserts_new_state(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.upsert(recording_id, todo_index=0, task_text="Write spec", done=False)
    items = repo.list_for_recording(recording_id)
    assert len(items) == 1
    assert items[0].task_text == "Write spec"
    assert items[0].done is False
    assert items[0].done_at is None


def test_mark_done_sets_timestamp(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.upsert(recording_id, todo_index=0, task_text="Write spec", done=False)
    repo.mark_done(recording_id, todo_index=0, done=True)
    items = repo.list_for_recording(recording_id)
    assert items[0].done is True
    assert items[0].done_at is not None


def test_mark_undone_clears_timestamp(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.upsert(recording_id, todo_index=0, task_text="Write spec", done=False)
    repo.mark_done(recording_id, todo_index=0, done=True)
    repo.mark_done(recording_id, todo_index=0, done=False)
    items = repo.list_for_recording(recording_id)
    assert items[0].done is False
    assert items[0].done_at is None


def test_upsert_is_idempotent_on_index(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.upsert(recording_id, todo_index=0, task_text="Write spec", done=False)
    repo.upsert(recording_id, todo_index=0, task_text="Write spec v2", done=False)
    items = repo.list_for_recording(recording_id)
    assert len(items) == 1
    assert items[0].task_text == "Write spec v2"


def test_mark_done_creates_row_if_missing(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.mark_done(recording_id, todo_index=2, done=True, task_text="Inserted lazily")
    items = repo.list_for_recording(recording_id)
    assert len(items) == 1
    assert items[0].todo_index == 2
    assert items[0].done is True


def test_mark_done_requires_task_text_if_row_missing(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    with pytest.raises(ValueError, match="task_text"):
        repo.mark_done(recording_id, todo_index=0, done=True)


def test_recording_delete_cascades(db: Database, recording_id: int) -> None:
    repo = TodoStateRepo(db)
    repo.upsert(recording_id, todo_index=0, task_text="X", done=True)
    RecordingRepo(db).delete(recording_id)
    assert repo.list_for_recording(recording_id) == []
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
uv run pytest tests/storage/test_todos.py -v
```
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Extend `models.py` with `TodoState`**

Append to `src/teams_transcriber/storage/models.py`:
```python


@dataclass(slots=True)
class TodoState:
    id: int | None
    recording_id: int
    todo_index: int
    task_text: str
    done: bool
    done_at: str | None
```

- [ ] **Step 4: Implement `todos.py`**

Create `src/teams_transcriber/storage/todos.py`:
```python
"""Repository for `todo_state` — tracks checkbox state for each my_todo across re-summaries."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import TodoState


def _row_to_todo(row: sqlite3.Row) -> TodoState:
    return TodoState(
        id=row["id"],
        recording_id=row["recording_id"],
        todo_index=row["todo_index"],
        task_text=row["task_text"],
        done=bool(row["done"]),
        done_at=row["done_at"],
    )


class TodoStateRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(
        self,
        recording_id: int,
        todo_index: int,
        task_text: str,
        done: bool,
    ) -> None:
        done_at = datetime.now(UTC).isoformat() if done else None
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO todo_state (recording_id, todo_index, task_text, done, done_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(recording_id, todo_index) DO UPDATE SET
                    task_text = excluded.task_text,
                    done      = excluded.done,
                    done_at   = excluded.done_at
                """,
                (recording_id, todo_index, task_text, int(done), done_at),
            )
            conn.commit()

    def mark_done(
        self,
        recording_id: int,
        todo_index: int,
        done: bool,
        *,
        task_text: str | None = None,
    ) -> None:
        """Set done state. If no row exists yet, `task_text` is required."""
        done_at = datetime.now(UTC).isoformat() if done else None
        with self._db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM todo_state WHERE recording_id = ? AND todo_index = ?",
                (recording_id, todo_index),
            ).fetchone()
            if existing is None:
                if task_text is None:
                    raise ValueError("task_text is required when no existing row matches")
                conn.execute(
                    """
                    INSERT INTO todo_state (recording_id, todo_index, task_text, done, done_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (recording_id, todo_index, task_text, int(done), done_at),
                )
            else:
                conn.execute(
                    "UPDATE todo_state SET done = ?, done_at = ? WHERE id = ?",
                    (int(done), done_at, existing["id"]),
                )
            conn.commit()

    def list_for_recording(self, recording_id: int) -> list[TodoState]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM todo_state WHERE recording_id = ? ORDER BY todo_index",
                (recording_id,),
            ).fetchall()
        return [_row_to_todo(r) for r in rows]
```

- [ ] **Step 5: Run the test to verify it passes**

```powershell
uv run pytest tests/storage/test_todos.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 6: Lint & type-check**

```powershell
uv run ruff check src tests
uv run mypy
```
Expected: both clean.

- [ ] **Step 7: Commit**

```powershell
git add src/teams_transcriber/storage/models.py src/teams_transcriber/storage/todos.py tests/storage/test_todos.py
git commit -m "feat(storage): add TodoState model and TodoStateRepo"
```

---

## Task 9: Audio retention pruner

**Files:**
- Create: `src/teams_transcriber/storage/retention.py`
- Test: `tests/storage/test_retention.py`

Deletes audio files older than `retention_days`, sets `audio_path = NULL`, `audio_deleted_at = now()` on the matching recording rows. **Does not** delete transcripts or summaries. Designed to be called periodically (Phase 2/3 will wire up a `QTimer`).

- [ ] **Step 1: Write the failing test**

Create `tests/storage/test_retention.py`:
```python
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import Recording, RecordingSource, RecordingStatus
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.retention import AudioRetentionPruner


def _isoz(dt: datetime) -> str:
    return dt.isoformat()


def _make_recording(
    db: Database, *, started_at: datetime, audio_path: Path | None
) -> int:
    rec = RecordingRepo(db).create(
        Recording(
            id=None,
            started_at=_isoz(started_at),
            ended_at=_isoz(started_at + timedelta(minutes=30)),
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title="X",
            audio_path=str(audio_path) if audio_path else None,
            audio_deleted_at=None,
            duration_ms=30 * 60 * 1000,
            status=RecordingStatus.DONE,
            error_message=None,
        )
    )
    assert rec.id is not None
    return rec.id


def test_prune_deletes_old_audio(db: Database, tmp_path: Path) -> None:
    old_audio = tmp_path / "old.opus"
    old_audio.write_bytes(b"x" * 1024)
    new_audio = tmp_path / "new.opus"
    new_audio.write_bytes(b"y" * 1024)

    now = datetime.now(UTC)
    old_id = _make_recording(db, started_at=now - timedelta(days=45), audio_path=old_audio)
    new_id = _make_recording(db, started_at=now - timedelta(days=5), audio_path=new_audio)

    pruner = AudioRetentionPruner(db, retention_days=30, now=lambda: now)
    report = pruner.run()

    assert not old_audio.exists()
    assert new_audio.exists()
    assert report.deleted_count == 1
    assert report.skipped_count == 1

    repo = RecordingRepo(db)
    old = repo.get(old_id)
    assert old is not None
    assert old.audio_path is None
    assert old.audio_deleted_at is not None

    new = repo.get(new_id)
    assert new is not None
    assert new.audio_path == str(new_audio)


def test_prune_ignores_already_pruned_recordings(db: Database, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    old_id = _make_recording(db, started_at=now - timedelta(days=45), audio_path=None)
    pruner = AudioRetentionPruner(db, retention_days=30, now=lambda: now)
    report = pruner.run()
    assert report.deleted_count == 0
    assert report.skipped_count == 0  # null-audio rows aren't even considered
    assert RecordingRepo(db).get(old_id) is not None  # row still exists


def test_prune_handles_missing_file_gracefully(db: Database, tmp_path: Path) -> None:
    """If the audio file is already gone on disk, we still null out the DB column."""
    now = datetime.now(UTC)
    ghost = tmp_path / "ghost.opus"  # never written
    rec_id = _make_recording(db, started_at=now - timedelta(days=45), audio_path=ghost)

    pruner = AudioRetentionPruner(db, retention_days=30, now=lambda: now)
    report = pruner.run()
    assert report.deleted_count == 0
    assert report.missing_count == 1

    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None
    assert rec.audio_path is None
    assert rec.audio_deleted_at is not None


def test_prune_does_not_touch_recordings_currently_in_progress(
    db: Database, tmp_path: Path
) -> None:
    """A recording with status='recording' must not be pruned even if started long ago."""
    audio = tmp_path / "live.opus"
    audio.write_bytes(b"x")
    now = datetime.now(UTC)
    rec = RecordingRepo(db).create(
        Recording(
            id=None,
            started_at=_isoz(now - timedelta(days=45)),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title="X",
            audio_path=str(audio),
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    pruner = AudioRetentionPruner(db, retention_days=30, now=lambda: now)
    report = pruner.run()
    assert report.deleted_count == 0
    assert audio.exists()
    assert RecordingRepo(db).get(rec.id).audio_path == str(audio)  # type: ignore[union-attr,arg-type]


def test_retention_days_zero_disables_pruning(db: Database, tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x")
    now = datetime.now(UTC)
    _make_recording(db, started_at=now - timedelta(days=365), audio_path=audio)
    pruner = AudioRetentionPruner(db, retention_days=0, now=lambda: now)
    report = pruner.run()
    assert report.deleted_count == 0
    assert audio.exists()
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
uv run pytest tests/storage/test_retention.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `retention.py`**

Create `src/teams_transcriber/storage/retention.py`:
```python
"""Prunes audio files past their retention window.

Transcripts and summaries are kept indefinitely — only the audio file itself is deleted
and the recording row's `audio_path` is nulled (with `audio_deleted_at` set).

Recordings still in progress (status == 'recording') are never pruned regardless of age.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import RecordingStatus


@dataclass(slots=True)
class PruneReport:
    deleted_count: int = 0
    missing_count: int = 0
    skipped_count: int = 0


class AudioRetentionPruner:
    def __init__(
        self,
        db: Database,
        retention_days: int,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if retention_days < 0:
            raise ValueError("retention_days must be >= 0")
        self._db = db
        self._retention_days = retention_days
        self._now = now

    def run(self) -> PruneReport:
        report = PruneReport()
        if self._retention_days == 0:
            return report

        now = self._now()
        cutoff = (now - timedelta(days=self._retention_days)).isoformat()
        now_iso = now.isoformat()

        with self._db.connect() as conn:
            # Rows eligible for pruning: have audio, older than cutoff, not in active recording.
            eligible = conn.execute(
                """
                SELECT id, audio_path FROM recordings
                WHERE audio_path IS NOT NULL
                  AND started_at < ?
                  AND status != ?
                """,
                (cutoff, RecordingStatus.RECORDING.value),
            ).fetchall()

            # Rows we deliberately did NOT touch — newer than cutoff or in active recording —
            # but which still have audio. Reported as "skipped" for visibility.
            skipped = conn.execute(
                """
                SELECT COUNT(*) AS n FROM recordings
                WHERE audio_path IS NOT NULL
                  AND (started_at >= ? OR status = ?)
                """,
                (cutoff, RecordingStatus.RECORDING.value),
            ).fetchone()["n"]
            report.skipped_count = skipped

            for row in eligible:
                path = Path(row["audio_path"])
                if path.exists():
                    try:
                        path.unlink()
                        report.deleted_count += 1
                    except OSError:
                        # Could not delete (locked, permissions, etc.) — leave row alone,
                        # do not null out audio_path so we'll retry next run.
                        report.skipped_count += 1
                        continue
                else:
                    report.missing_count += 1

                conn.execute(
                    """
                    UPDATE recordings
                    SET audio_path = NULL, audio_deleted_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, row["id"]),
                )
            conn.commit()

        return report
```

- [ ] **Step 4: Run the test to verify it passes**

```powershell
uv run pytest tests/storage/test_retention.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Lint & type-check**

```powershell
uv run ruff check src tests
uv run mypy
```
Expected: both clean.

- [ ] **Step 6: Commit**

```powershell
git add src/teams_transcriber/storage/retention.py tests/storage/test_retention.py
git commit -m "feat(storage): add AudioRetentionPruner with safety rules"
```

---

## Task 10: Public storage API + end-to-end lifecycle test

**Files:**
- Modify: `src/teams_transcriber/storage/__init__.py` (re-export public surface)
- Test: `tests/storage/test_lifecycle.py`

Exposes the public storage API so downstream code does `from teams_transcriber.storage import ...`. The lifecycle test exercises every repo together to confirm they integrate.

- [ ] **Step 1: Update `storage/__init__.py` to re-export the public surface**

Replace `src/teams_transcriber/storage/__init__.py` with:
```python
"""Storage layer: SQLite-backed persistence for recordings, transcripts, summaries, todos.

Usage:
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import build_database, RecordingRepo, TranscriptRepo

    paths = AppPaths()
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    recordings = RecordingRepo(db)
    ...
"""

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.migrations import Migration, MigrationRunner
from teams_transcriber.storage.models import (
    ActionItemOther,
    Channel,
    Recording,
    RecordingSource,
    RecordingStatus,
    Summary,
    TodoItem,
    TodoState,
    TranscriptSegment,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.retention import AudioRetentionPruner, PruneReport
from teams_transcriber.storage.schema_v1 import SCHEMA_V1
from teams_transcriber.storage.summaries import SummaryRepo
from teams_transcriber.storage.todos import TodoStateRepo
from teams_transcriber.storage.transcripts import SearchHit, TranscriptRepo

ALL_MIGRATIONS = (SCHEMA_V1,)


def build_database(path: "Path") -> Database:  # noqa: F821 — quoted to keep import light
    """Construct a Database with the canonical migration set applied."""
    from pathlib import Path  # local import to keep module head light

    if not isinstance(path, Path):
        path = Path(path)
    return Database(path, migrations=ALL_MIGRATIONS)


__all__ = [
    "ALL_MIGRATIONS",
    "ActionItemOther",
    "AudioRetentionPruner",
    "Channel",
    "Database",
    "Migration",
    "MigrationRunner",
    "PruneReport",
    "Recording",
    "RecordingRepo",
    "RecordingSource",
    "RecordingStatus",
    "SCHEMA_V1",
    "SearchHit",
    "Summary",
    "SummaryRepo",
    "TodoItem",
    "TodoState",
    "TodoStateRepo",
    "TranscriptRepo",
    "TranscriptSegment",
    "build_database",
]
```

- [ ] **Step 2: Write the lifecycle smoke test**

Create `tests/storage/test_lifecycle.py`:
```python
"""End-to-end: exercise every repo together against one Database to confirm integration."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from teams_transcriber.storage import (
    ActionItemOther,
    AudioRetentionPruner,
    Channel,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    Summary,
    SummaryRepo,
    TodoItem,
    TodoStateRepo,
    TranscriptRepo,
    TranscriptSegment,
    build_database,
)


def test_full_lifecycle(tmp_path: Path) -> None:
    db = build_database(tmp_path / "tt.db")
    db.initialize()

    recordings = RecordingRepo(db)
    transcripts = TranscriptRepo(db)
    summaries = SummaryRepo(db)
    todos = TodoStateRepo(db)

    started = datetime(2026, 5, 14, 10, 0, tzinfo=UTC)
    audio = tmp_path / "rec.opus"
    audio.write_bytes(b"audio")

    # 1. Create a recording in 'recording' state.
    rec = recordings.create(
        Recording(
            id=None,
            started_at=started.isoformat(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="Q2 roadmap sync | Microsoft Teams",
            display_title=None,
            audio_path=str(audio),
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    assert rec.id is not None

    # 2. Append live transcript segments.
    transcripts.append_many(
        [
            TranscriptSegment(None, rec.id, 0, 2000, Channel.OTHERS,
                              "Welcome everyone, let's talk about the billing rewrite."),
            TranscriptSegment(None, rec.id, 2000, 4500, Channel.ME,
                              "Sure, I'll own the API stub by Friday."),
            TranscriptSegment(None, rec.id, 4500, 7000, Channel.OTHERS,
                              "Sarah will handle the migration doc."),
        ]
    )

    # 3. Move through state transitions.
    recordings.update_status(rec.id, RecordingStatus.TRANSCRIBING)
    recordings.finalize(rec.id, ended_at=(started + timedelta(minutes=47)).isoformat(),
                         duration_ms=47 * 60 * 1000)
    recordings.update_status(rec.id, RecordingStatus.SUMMARIZING)

    # 4. Persist a summary.
    summaries.upsert(
        Summary(
            recording_id=rec.id,
            one_line="Aligned on billing rewrite; I own API stub by Friday.",
            summary="Discussed the billing rewrite. Agreed to ship in July.",
            key_decisions=["Billing rewrite scheduled for July release"],
            my_todos=[TodoItem(task="Write API stub spec", due="2026-05-16")],
            action_items_others=[ActionItemOther(who="Sarah", task="Review migration doc")],
            follow_ups=["Revisit pricing tiers after legal review"],
            topics=["billing", "roadmap"],
            generated_at=datetime.now(UTC).isoformat(),
            model_used="claude-sonnet-4-6",
        )
    )
    recordings.update_status(rec.id, RecordingStatus.DONE)
    recordings.set_display_title(rec.id, "Q2 roadmap sync")

    # 5. Mark the my_todo as done.
    todos.mark_done(rec.id, todo_index=0, done=True, task_text="Write API stub spec")

    # 6. Search across the transcript.
    hits = transcripts.search("billing rewrite")
    assert len(hits) >= 1
    assert any(h.recording_id == rec.id for h in hits)

    # 7. Read back everything end-to-end.
    fetched = recordings.get(rec.id)
    assert fetched is not None
    assert fetched.display_title == "Q2 roadmap sync"
    assert fetched.status == RecordingStatus.DONE

    summary = summaries.get(rec.id)
    assert summary is not None
    assert summary.my_todos[0].task == "Write API stub spec"

    state = todos.list_for_recording(rec.id)
    assert state[0].done is True

    segs = transcripts.list_for_recording(rec.id)
    assert len(segs) == 3

    # 8. Run retention 100 days later and confirm audio is pruned, transcripts/summaries kept.
    future = started + timedelta(days=100)
    pruner = AudioRetentionPruner(db, retention_days=30, now=lambda: future)
    report = pruner.run()
    assert report.deleted_count == 1
    assert not audio.exists()

    refetched = recordings.get(rec.id)
    assert refetched is not None
    assert refetched.audio_path is None
    assert refetched.audio_deleted_at is not None
    # Transcripts and summaries must still be present.
    assert len(transcripts.list_for_recording(rec.id)) == 3
    assert summaries.get(rec.id) is not None

    db.close()


def test_database_can_be_reopened(tmp_path: Path) -> None:
    """Open, write, close, reopen — data persists; migrations don't re-run destructively."""
    db_path = tmp_path / "persist.db"

    db = build_database(db_path)
    db.initialize()
    repo = RecordingRepo(db)
    rec = repo.create(
        Recording(
            id=None,
            started_at="2026-05-14T10:00:00+00:00",
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title=None,
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.DONE,
            error_message=None,
        )
    )
    rec_id = rec.id
    db.close()

    db2 = build_database(db_path)
    db2.initialize()
    again = RecordingRepo(db2).get(rec_id)  # type: ignore[arg-type]
    assert again is not None
    assert again.detected_title == "X"
    db2.close()
```

- [ ] **Step 3: Run the lifecycle tests**

```powershell
uv run pytest tests/storage/test_lifecycle.py -v
```
Expected: both tests PASS.

- [ ] **Step 4: Run the full suite to confirm nothing regressed**

```powershell
uv run pytest -v
```
Expected: all tests PASS (paths: 4, migrations: 6, db: 7, recordings: 9, transcripts: 8, summaries: 4, todos: 7, retention: 5, lifecycle: 2 = ~52 tests).

- [ ] **Step 5: Lint & type-check**

```powershell
uv run ruff check src tests
uv run mypy
```
Expected: both clean.

- [ ] **Step 6: Commit**

```powershell
git add src/teams_transcriber/storage/__init__.py tests/storage/test_lifecycle.py
git commit -m "feat(storage): re-export public API and add full lifecycle smoke test"
```

---

## Self-review (executed by the agent before handing off)

### Spec coverage (Phase 1 scope)

| Spec section | Implemented by |
|---|---|
| §8.1 file layout (paths) | Task 2 (`AppPaths`) |
| §8.2 `recordings` table | Tasks 4, 5 |
| §8.2 `transcript_segments` table | Tasks 4, 6 |
| §8.2 `transcript_fts` + triggers | Task 4 |
| §8.2 `summaries` table | Tasks 4, 7 |
| §8.2 `todo_state` table | Tasks 4, 8 |
| §8.2 `PRAGMA user_version` migrations | Task 3 |
| §8.3 retention (audio-only, never transcripts/summaries) | Task 9 (+ verified in Task 10) |
| §7.4 FTS-backed global search query path | Task 6 (`TranscriptRepo.search`) |

Not in Phase 1 (deferred by design): the actual *scheduling* of retention; loading/saving settings.json; AppPaths integration into a running app; the keyring-backed API key. All belong to Phase 2 or 3.

### Placeholder scan

- No "TBD" / "TODO" markers in plan text.
- Every code step has complete code (no "…fill in here…").
- Every test step has runnable assertions.
- Every command step has expected output.

### Type & name consistency

- `Channel`, `RecordingSource`, `RecordingStatus` defined once in `models.py` and imported elsewhere.
- `Database.connect()` returns a context manager yielding `sqlite3.Connection` — consistent across all repos.
- `Migration` constructor signature `(version, name, apply)` consistent across schema_v1 + tests.
- `SearchHit.recording_title` matches the JOIN aliased as `r.display_title AS recording_title`.
- `AudioRetentionPruner.run()` returns `PruneReport` — referenced consistently in tests.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-14-phase-1-foundation.md`.

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
