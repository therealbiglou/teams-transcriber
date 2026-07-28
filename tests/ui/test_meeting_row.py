# tests/ui/test_meeting_row.py
from __future__ import annotations

from teams_transcriber.storage.models import Recording, RecordingSource, RecordingStatus
from teams_transcriber.ui.meeting_row import MeetingRow, format_when
from teams_transcriber.ui.meeting_status import RowAction, derive_row_state


def _rec(rid=1, status=RecordingStatus.DONE, duration_ms=2_280_000):
    return Recording(
        id=rid, started_at="2026-07-26T15:31:00+00:00", ended_at=None,
        source=RecordingSource.TEAMS, detected_title="X", display_title="Q3 Sync",
        audio_path=None, audio_deleted_at=None, duration_ms=duration_ms,
        status=status, error_message=None,
    )


def test_format_when_includes_date_and_duration():
    text = format_when("2026-07-26T15:31:00+00:00", 2_280_000)
    assert "Jul 26" in text
    assert "38 min" in text


def test_format_when_omits_duration_when_unknown():
    assert "min" not in format_when("2026-07-26T15:31:00+00:00", None)


def test_row_shows_title_and_chip(qtbot):
    rec = _rec()
    row = MeetingRow(rec, derive_row_state(status=RecordingStatus.DONE))
    qtbot.addWidget(row)
    assert row.title_label.text() == "Q3 Sync"
    assert row.chip_label.text() == "Not in Wrike"


def test_action_button_emits_action_with_id(qtbot):
    rec = _rec(rid=7)
    row = MeetingRow(rec, derive_row_state(status=RecordingStatus.DONE))
    qtbot.addWidget(row)
    with qtbot.waitSignal(row.action_requested) as sig:
        row.action_button.click()
    assert sig.args == [7, RowAction.SEND_TO_WRIKE.value]


def test_no_action_button_when_action_is_none(qtbot):
    rec = _rec(status=RecordingStatus.RECORDING)
    row = MeetingRow(rec, derive_row_state(status=RecordingStatus.RECORDING))
    qtbot.addWidget(row)
    assert row.action_button is None


def test_delete_button_emits_delete_with_id(qtbot):
    rec = _rec(rid=9)
    row = MeetingRow(rec, derive_row_state(status=RecordingStatus.DONE))
    qtbot.addWidget(row)
    with qtbot.waitSignal(row.delete_requested) as sig:
        row.delete_button.click()
    assert sig.args == [9]


def test_failed_chip_opens_detail_dialog(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "teams_transcriber.ui.meeting_row.ConfirmDialog.info",
        lambda parent, **kw: calls.append(kw),
    )
    rec = _rec(status=RecordingStatus.SUMMARY_FAILED)
    state = derive_row_state(
        status=RecordingStatus.SUMMARY_FAILED, error_message="429 rate limited",
    )
    row = MeetingRow(rec, state)
    qtbot.addWidget(row)
    row.show_error_detail()
    assert len(calls) == 1
    assert "429 rate limited" in calls[0]["body"]
    assert calls[0]["selectable"] is True


def test_non_failed_chip_does_not_open_dialog(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "teams_transcriber.ui.meeting_row.ConfirmDialog.info",
        lambda parent, **kw: calls.append(kw),
    )
    row = MeetingRow(_rec(), derive_row_state(status=RecordingStatus.DONE))
    qtbot.addWidget(row)
    row.show_error_detail()
    assert calls == []
