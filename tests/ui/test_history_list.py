from __future__ import annotations

from PySide6.QtCore import Qt

from teams_transcriber.storage.models import Recording, RecordingSource, RecordingStatus
from teams_transcriber.ui.history_list import HistoryList
from teams_transcriber.ui.meeting_status import RowAction, derive_row_state


def test_horizontal_scrollbar_policy_is_as_needed(qtbot):
    """CLAUDE.md's scroll-area guidance (guard #1 of three) requires
    ScrollBarAsNeeded so overflow becomes visible instead of being silently
    clipped -- ScrollBarAlwaysOff would hide a regression in guard #2."""
    lst = HistoryList()
    qtbot.addWidget(lst)
    assert lst.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded


def _rec(rid, started_at, status=RecordingStatus.DONE):
    return Recording(
        id=rid, started_at=started_at, ended_at=None, source=RecordingSource.TEAMS,
        detected_title="x", display_title=f"Meeting {rid}", audio_path=None,
        audio_deleted_at=None, duration_ms=60_000, status=status, error_message=None,
    )


def test_set_rows_renders_one_row_per_recording(qtbot):
    lst = HistoryList()
    qtbot.addWidget(lst)
    rows = [
        (_rec(1, "2026-07-26T15:00:00+00:00"), derive_row_state(status=RecordingStatus.DONE)),
        (_rec(2, "2026-07-20T15:00:00+00:00"), derive_row_state(status=RecordingStatus.DONE)),
    ]
    lst.set_rows(rows)
    from teams_transcriber.ui.meeting_row import MeetingRow
    assert len(lst.findChildren(MeetingRow)) == 2


def test_set_rows_replaces_previous_content(qtbot):
    lst = HistoryList()
    qtbot.addWidget(lst)
    state = derive_row_state(status=RecordingStatus.DONE)
    lst.set_rows([(_rec(1, "2026-07-26T15:00:00+00:00"), state)])
    lst.set_rows([(_rec(2, "2026-07-26T15:00:00+00:00"), state)])
    from teams_transcriber.ui.meeting_row import MeetingRow
    assert len(lst.findChildren(MeetingRow)) == 1


def test_row_action_is_reemitted_by_the_list(qtbot):
    lst = HistoryList()
    qtbot.addWidget(lst)
    lst.set_rows([(_rec(5, "2026-07-26T15:00:00+00:00"),
                   derive_row_state(status=RecordingStatus.DONE))])
    from teams_transcriber.ui.meeting_row import MeetingRow
    row = lst.findChildren(MeetingRow)[0]
    with qtbot.waitSignal(lst.action_requested) as sig:
        row.action_button.click()
    assert sig.args == [5, RowAction.SEND_TO_WRIKE.value]


def test_history_list_pins_container_to_viewport(qtbot):
    """Guard #2 of the three documented scroll-area guards (CLAUDE.md): the
    inner container must be pinned to the viewport width on resize so rows
    wrap instead of overflowing past the (as-needed) horizontal scrollbar --
    a regression here silently clips rows rather than showing a scrollbar."""
    lst = HistoryList()
    qtbot.addWidget(lst)
    lst.resize(300, 400)
    lst.show()
    qtbot.waitExposed(lst)
    assert lst._container.maximumWidth() <= lst.viewport().width()


def test_row_delete_is_reemitted_by_the_list(qtbot):
    lst = HistoryList()
    qtbot.addWidget(lst)
    lst.set_rows([(_rec(6, "2026-07-26T15:00:00+00:00"),
                   derive_row_state(status=RecordingStatus.DONE))])
    from teams_transcriber.ui.meeting_row import MeetingRow
    row = lst.findChildren(MeetingRow)[0]
    with qtbot.waitSignal(lst.delete_requested) as sig:
        row.delete_button.click()
    assert sig.args == [6]
