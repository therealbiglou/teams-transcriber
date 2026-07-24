"""Shared pytest fixtures."""

from __future__ import annotations

import os

# Run all Qt-aware tests offscreen so they don't spawn real windows.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections.abc import Iterator
from pathlib import Path

import pytest

from teams_transcriber.storage.db import Database


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    """An initialized Database with the full migration set applied. Cleaned up after the test."""
    # Imported lazily to keep this conftest importable before storage exists.
    from teams_transcriber.storage import build_database

    database = build_database(tmp_path / "test.db")
    database.initialize()
    try:
        yield database
    finally:
        database.close()
