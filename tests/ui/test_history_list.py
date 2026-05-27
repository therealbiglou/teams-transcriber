from __future__ import annotations

from datetime import UTC, datetime, timedelta

from teams_transcriber.storage.models import (
    Recording,
    RecordingSource,
    RecordingStatus,
)
from teams_transcriber.ui.history_list import HistoryList, _bucket_label, filter_for_bucket
from teams_transcriber.ui.sidebar import SidebarBucket


def _rec(
    rid: int, when: str,
    status: RecordingStatus = RecordingStatus.DONE,
    source: RecordingSource = RecordingSource.TEAMS,
) -> Recording:
    return Recording(
        id=rid, started_at=when, ended_at=when,
        source=source, detected_title=f"M {rid}", display_title=f"M {rid}",
        audio_path=None, audio_deleted_at=None, duration_ms=60000,
        status=status, error_message=None,
    )


def test_buckets_partition_by_date() -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC).astimezone()
    today_iso = now.isoformat()
    yesterday_iso = (now - timedelta(days=1)).isoformat()
    week_iso = (now - timedelta(days=4)).isoformat()
    old_iso = (now - timedelta(days=30)).isoformat()

    assert _bucket_label(today_iso, now) == "Today"
    assert _bucket_label(yesterday_iso, now) == "Yesterday"
    assert _bucket_label(week_iso, now) == "This week"
    assert _bucket_label(old_iso, now) == "Earlier"


def test_filter_for_bucket_failed() -> None:
    rows = [
        (_rec(1, "2026-05-15T10:00:00+00:00", RecordingStatus.DONE), None, 0, 0),
        (_rec(2, "2026-05-15T10:00:00+00:00", RecordingStatus.SUMMARY_FAILED), None, 0, 0),
    ]
    out = filter_for_bucket(rows, SidebarBucket.FAILED)
    assert [r[0].id for r in out] == [2]


def test_filter_for_bucket_manual() -> None:
    rows = [
        (_rec(1, "2026-05-15T10:00:00+00:00", source=RecordingSource.TEAMS), None, 0, 0),
        (_rec(2, "2026-05-15T10:00:00+00:00", source=RecordingSource.MANUAL), None, 0, 0),
    ]
    out = filter_for_bucket(rows, SidebarBucket.MANUAL)
    assert [r[0].id for r in out] == [2]


def test_history_list_renders_and_emits(qapp, qtbot) -> None:
    from PySide6.QtCore import Qt
    lst = HistoryList()
    received: list[int] = []
    lst.recording_selected.connect(received.append)

    rows = [(_rec(1, "2026-05-15T10:00:00+00:00"), "one-liner", 2, 0)]
    lst.set_recordings(rows)

    from teams_transcriber.ui.meeting_card import MeetingCard
    cards = lst.findChildren(MeetingCard)
    assert len(cards) == 1
    qtbot.mouseClick(cards[0], Qt.MouseButton.LeftButton)
    assert received == [1]


def test_history_list_accepts_four_tuple(qapp) -> None:
    """set_recordings must accept 4-tuples (Recording, one_line, todo_count, todos_done)."""
    lst = HistoryList()
    rows = [(_rec(1, "2026-05-15T10:00:00+00:00"), "one line", 3, 1)]
    lst.set_recordings(rows)
    assert 1 in lst._cards
