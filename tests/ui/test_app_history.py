"""The history list is populated from the DB with derived row states."""
from __future__ import annotations

from teams_transcriber.ui.meeting_status import RowAction


def test_refresh_history_derives_state_per_recording(app_with_db, qtbot):
    """A done recording with no Wrike project offers Send to Wrike."""
    app = app_with_db
    app._refresh_history()
    from teams_transcriber.ui.meeting_row import MeetingRow
    rows = app.history.findChildren(MeetingRow)
    assert rows, "expected at least one row"
    assert rows[0].chip_label.text() == "Not in Wrike"


def test_row_action_send_dispatches_to_wrike_worker(app_with_db, monkeypatch):
    app = app_with_db
    called = []
    monkeypatch.setattr(app, "_wrike_export_worker", lambda rid: called.append(rid))
    app._on_row_action(1, RowAction.SEND_TO_WRIKE.value)
    assert called == [1]


def test_row_action_retry_dispatches_to_retry(app_with_db, monkeypatch):
    app = app_with_db
    called = []
    monkeypatch.setattr(app, "_retry_recording", lambda rid: called.append(rid))
    app._on_row_action(1, RowAction.RETRY.value)
    assert called == [1]


def test_delete_asks_first_and_keeps_row_when_declined(app_with_db, monkeypatch):
    app = app_with_db
    monkeypatch.setattr(
        "teams_transcriber.ui.app.ConfirmDialog.ask", lambda *a, **k: False,
    )
    app._on_row_delete(1)
    from teams_transcriber.storage.recordings import RecordingRepo
    assert RecordingRepo(app.db).get(1) is not None


def test_delete_removes_recording_and_audio_when_confirmed(
    app_with_db, monkeypatch, tmp_path,
):
    app = app_with_db
    audio = tmp_path / "rec.opus"
    audio.write_bytes(b"x")
    from teams_transcriber.storage.recordings import RecordingRepo
    RecordingRepo(app.db).set_audio_path(1, str(audio))
    monkeypatch.setattr(
        "teams_transcriber.ui.app.ConfirmDialog.ask", lambda *a, **k: True,
    )
    app._on_row_delete(1)
    assert RecordingRepo(app.db).get(1) is None
    assert not audio.exists()
