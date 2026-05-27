"""Tests for the extracted NotesEditor widget."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    build_database,
)
from teams_transcriber.ui.notes_editor import NotesEditor


@pytest.fixture
def env(tmp_path, qapp):
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    yield db
    db.close()


def _make_recording(db) -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-18T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t", audio_path=None,
        audio_deleted_at=None, duration_ms=None,
        status=RecordingStatus.SUMMARIZING, error_message=None,
    ))
    assert rec.id is not None
    return rec.id


def _cursor_into_list(te) -> None:
    cursor = te.textCursor()
    cursor.movePosition(cursor.MoveOperation.Start)
    te.setTextCursor(cursor)
    if te.textCursor().currentList() is None:
        cursor.movePosition(cursor.MoveOperation.NextBlock)
        te.setTextCursor(cursor)


def test_tab_indents_and_shift_tab_outdents_list_item(env):
    db = env
    editor = NotesEditor(db, _make_recording(db))
    te = editor.editor
    te.setHtml("<ul><li>one</li><li>two</li></ul>")
    _cursor_into_list(te)
    lst = te.textCursor().currentList()
    assert lst is not None
    start = lst.format().indent()

    assert te.change_list_indent(1) is True
    assert te.textCursor().currentList().format().indent() == start + 1

    assert te.change_list_indent(-1) is True
    assert te.textCursor().currentList().format().indent() == start


def test_change_list_indent_noop_outside_list(env):
    db = env
    editor = NotesEditor(db, _make_recording(db))
    editor.editor.setPlainText("just a line, no list")
    assert editor.editor.change_list_indent(1) is False


def _process_events_briefly(ms: int = 50) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_notes_editor_loads_existing_notes(env) -> None:
    db = env
    rid = _make_recording(db)
    RecordingRepo(db).set_manual_notes(rid, "<p>previously typed</p>")

    editor = NotesEditor(db, rid)
    assert "previously typed" in editor.editor.toPlainText()


def test_notes_editor_autosaves_on_text_change(env) -> None:
    db = env
    rid = _make_recording(db)
    editor = NotesEditor(db, rid, autosave_debounce_ms=20)
    editor.editor.setPlainText("hello phase 5")
    _process_events_briefly(80)

    rec = RecordingRepo(db).get(rid)
    assert rec.manual_notes is not None
    assert "hello phase 5" in rec.manual_notes


def test_notes_editor_save_on_close_safety(env) -> None:
    db = env
    rid = _make_recording(db)
    editor = NotesEditor(db, rid, autosave_debounce_ms=10_000)  # long debounce
    editor.editor.setPlainText("close-saved text")
    editor.flush_now()

    rec = RecordingRepo(db).get(rid)
    assert rec.manual_notes is not None
    assert "close-saved text" in rec.manual_notes
