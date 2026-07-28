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


def test_row_action_open_in_wrike_toasts_when_no_permalink(app_with_db, monkeypatch):
    """Clicking "Open in Wrike" with no project/permalink recorded must not
    silently do nothing -- it should surface an in-app toast explaining why."""
    app = app_with_db
    toasts = []
    monkeypatch.setattr(
        "teams_transcriber.ui.app.show_in_app_toast",
        lambda *a, **k: toasts.append((a, k)),
    )
    app._on_row_action(1, RowAction.OPEN_IN_WRIKE.value)
    assert len(toasts) == 1
    assert toasts[0][0][0] == "Can't open in Wrike"


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


def test_refresh_history_threads_wrike_error_into_row_state(app_with_db):
    """A failed Wrike sync's error_message must flow into the row's chip and
    state -- if ``_refresh_history`` ever drops the ``wrike_error_message=``
    argument to ``derive_row_state``, the chip would still read "Wrike
    failed" but the click-for-details error text would silently vanish."""
    app = app_with_db
    from teams_transcriber.storage.wrike import WrikeSyncRepo
    WrikeSyncRepo(app.db).upsert(1, status="failed", error_message="boom")

    app._refresh_history()

    from teams_transcriber.ui.meeting_row import MeetingRow
    rows = app.history.findChildren(MeetingRow)
    row = next(r for r in rows if r._recording_id == 1)
    assert row.chip_label.text() == "Wrike failed"
    assert row._state.error_message == "boom"


def test_refresh_history_threads_recording_error_into_row_state(app_with_db):
    """A transcription/summary failure's own error_message must flow into
    the row state (independent of any Wrike error)."""
    app = app_with_db
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
    )
    rec = RecordingRepo(app.db).create(Recording(
        id=None, started_at="2026-07-27T15:00:00+00:00", ended_at=None,
        source=RecordingSource.TEAMS, detected_title="t2", display_title="Broke",
        audio_path=None, audio_deleted_at=None, duration_ms=None,
        status=RecordingStatus.TRANSCRIPTION_FAILED, error_message="oops",
    ))

    app._refresh_history()

    from teams_transcriber.ui.meeting_row import MeetingRow
    rows = app.history.findChildren(MeetingRow)
    row = next(r for r in rows if r._recording_id == rec.id)
    assert row._state.error_message == "oops"
