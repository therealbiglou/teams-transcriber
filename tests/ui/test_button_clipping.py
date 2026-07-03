"""Regression guard: text buttons must never be constrained below their
style-derived sizeHint — a fixed height smaller than the QSS padding + font
metrics clips glyphs (the 0.10.0 'button text cut off' report)."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QPushButton

from teams_transcriber.ui.theme import app_stylesheet


@pytest.fixture(autouse=True)
def _themed_app(qapp):
    """sizeHint only reflects the themed padding when the app QSS is applied —
    a bare qapp uses native metrics and would hide the clipping."""
    qapp.setStyleSheet(app_stylesheet())
    yield
    qapp.setStyleSheet("")


def _assert_no_vertical_clip(root) -> None:
    for btn in root.findChildren(QPushButton):
        if not btn.text():
            continue  # icon-only buttons size to their icon, not text metrics
        assert btn.maximumHeight() >= btn.sizeHint().height(), (
            f"button {btn.text()!r} constrained to {btn.maximumHeight()}px "
            f"but its style needs {btn.sizeHint().height()}px — text will clip"
        )


def test_banner_open_button_not_clipped(qapp):
    from teams_transcriber.ui.active_recording_banner import ActiveRecordingBanner
    banner = ActiveRecordingBanner()
    banner.show_recording(1, "Meeting")
    try:
        _assert_no_vertical_clip(banner)
    finally:
        banner.hide_banner()


def test_notes_toolbar_buttons_not_clipped(qapp, tmp_path):
    from teams_transcriber.storage import (
        Recording, RecordingRepo, RecordingSource, RecordingStatus, build_database,
    )
    from teams_transcriber.ui.notes_editor import NotesEditor

    db = build_database(tmp_path / "d.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-07-03T00:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title=None, display_title="D",
        audio_path=None, audio_deleted_at=None, duration_ms=0,
        status=RecordingStatus.RECORDING, error_message=None,
    ))
    editor = NotesEditor(db, rec.id)
    _assert_no_vertical_clip(editor)
    db.close()
