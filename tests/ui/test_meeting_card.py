from __future__ import annotations

from PySide6.QtCore import Qt

from teams_transcriber.storage.models import (
    Recording,
    RecordingSource,
    RecordingStatus,
)
from teams_transcriber.ui.meeting_card import MeetingCard


def _make_recording(**kwargs: object) -> Recording:
    defaults: dict[str, object] = dict(
        id=1,
        started_at="2026-05-15T10:00:00+00:00",
        ended_at="2026-05-15T10:30:00+00:00",
        source=RecordingSource.TEAMS,
        detected_title="X | Microsoft Teams",
        display_title="Q2 sync",
        audio_path=None,
        audio_deleted_at=None,
        duration_ms=30 * 60 * 1000,
        status=RecordingStatus.DONE,
        error_message=None,
    )
    defaults.update(kwargs)
    return Recording(**defaults)  # type: ignore[arg-type]


def test_card_emits_recording_id_on_click(qapp, qtbot) -> None:
    card = MeetingCard(_make_recording(), one_line="Aligned on X", todo_count=2)
    with qtbot.waitSignal(card.clicked, timeout=1000) as blocker:
        qtbot.mouseClick(card, Qt.MouseButton.LeftButton)
    assert blocker.args == [1]


def test_card_shows_failed_chip(qapp, qtbot) -> None:
    card = MeetingCard(
        _make_recording(status=RecordingStatus.SUMMARY_FAILED),
        one_line=None, todo_count=0,
    )
    from PySide6.QtWidgets import QLabel
    chips = [w for w in card.findChildren(QLabel) if w.property("role") == "chip"]
    assert any("Failed" in c.text() for c in chips)
