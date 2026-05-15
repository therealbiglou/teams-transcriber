"""UI test fixtures: ensure a QApplication exists for any test that paints."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _ensure_qapp(qapp: object) -> None:
    """Force pytest-qt's ``qapp`` fixture to run for every UI test.

    ``QPainter`` / ``QPixmap`` require a ``QGuiApplication`` to exist; without
    one the process crashes (exit code 9) before any test output is produced.
    """
