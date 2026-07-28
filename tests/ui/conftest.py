"""UI test fixtures: ensure a QApplication exists for any test that paints."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _ensure_qapp(qapp: object) -> None:
    """Force pytest-qt's ``qapp`` fixture to run for every UI test.

    ``QPainter`` / ``QPixmap`` require a ``QGuiApplication`` to exist; without
    one the process crashes (exit code 9) before any test output is produced.
    """


@pytest.fixture
def app_with_db(tmp_path, qapp):
    """A bare ``App`` (``__new__``, no ``__init__``) wired to a temp DB seeded
    with one ``DONE`` recording (id 1) plus a real ``HistoryList``/``window``
    so ``_refresh_history``/``_on_row_action``/``_on_row_delete`` can run
    without constructing the full app (tray, pipeline, first-run wizard)."""
    from PySide6.QtWidgets import QWidget

    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        build_database,
    )
    from teams_transcriber.ui.app import App
    from teams_transcriber.ui.history_list import HistoryList

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-07-26T15:00:00+00:00", ended_at=None,
        source=RecordingSource.TEAMS, detected_title="t", display_title="Q3 Sync",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id == 1

    app = App.__new__(App)
    app.db = db
    app.window = QWidget()
    app.history = HistoryList()

    yield app

    db.close()
