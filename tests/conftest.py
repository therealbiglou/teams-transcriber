"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from teams_transcriber.storage.db import Database


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    """An initialized Database with the v1 schema applied. Cleaned up after the test."""
    # Imported lazily so this conftest is importable before Task 4 lands schema_v1.
    from teams_transcriber.storage.schema_v1 import SCHEMA_V1

    database = Database(tmp_path / "test.db", migrations=[SCHEMA_V1])
    database.initialize()
    try:
        yield database
    finally:
        database.close()
