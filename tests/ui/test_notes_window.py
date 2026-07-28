from __future__ import annotations

from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.ui.notes_window import NotesWindow


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
