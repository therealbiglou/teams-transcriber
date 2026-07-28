from __future__ import annotations

from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.ui.notes_window import NotesWindow


def _done_recording(tmp_path, qapp):
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import (
        Recording,
        RecordingSource,
        RecordingStatus,
        build_database,
    )

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-07-26T15:00:00+00:00", ended_at="2026-07-26T15:30:00+00:00",
        source=RecordingSource.MANUAL, detected_title=None, display_title="Q3 Sync",
        audio_path=None, audio_deleted_at=None, duration_ms=1_800_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    return db, rec.id


def test_notes_are_persisted_on_save(tmp_db_with_recording, qtbot):
    db, rid = tmp_db_with_recording
    win = NotesWindow(db, rid)
    qtbot.addWidget(win)
    win.set_text("Whitney owns the packaging deadline")
    win.save()
    assert "Whitney owns" in (RecordingRepo(db).get(rid).manual_notes or "")


def test_stop_button_emits_stop_requested(tmp_db_with_recording, qtbot):
    db, rid = tmp_db_with_recording
    win = NotesWindow(db, rid)
    qtbot.addWidget(win)
    with qtbot.waitSignal(win.stop_requested):
        win.stop_button.click()


def test_stop_saves_notes_first(tmp_db_with_recording, qtbot):
    db, rid = tmp_db_with_recording
    win = NotesWindow(db, rid)
    qtbot.addWidget(win)
    win.set_text("late note")
    win.stop_button.click()
    assert "late note" in (RecordingRepo(db).get(rid).manual_notes or "")


def test_stop_button_visible_while_recording(tmp_db_with_recording, qtbot):
    db, rid = tmp_db_with_recording
    win = NotesWindow(db, rid)
    qtbot.addWidget(win)
    assert win.stop_button.isVisibleTo(win)


def test_stop_button_hidden_on_finished_recording(tmp_path, qapp, qtbot):
    """A notes window opened on an already-summarized/exported meeting must
    not present a Stop button -- clicking it would silently do nothing,
    since notes are never re-read or re-posted to Wrike after the meeting
    ends."""
    db, rid = _done_recording(tmp_path, qapp)
    try:
        win = NotesWindow(db, rid)
        qtbot.addWidget(win)
        assert not win.stop_button.isVisibleTo(win)
    finally:
        db.close()
