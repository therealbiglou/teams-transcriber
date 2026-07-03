"""Scrollable list of MeetingCards grouped by date bucket headers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from teams_transcriber.storage.models import Recording
from teams_transcriber.ui.meeting_card import MeetingCard
from teams_transcriber.ui.sidebar import SidebarBucket


class HistoryList(QScrollArea):
    """List of MeetingCards with optional date-bucket headers."""

    recording_selected = Signal(int)  # recording_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)
        self._layout.addStretch(1)
        self.setWidget(self._container)
        self._cards: dict[int, MeetingCard] = {}
        self._selected_id: int | None = None

    def resizeEvent(self, e: QResizeEvent) -> None:
        # Guard #2: pin the container to the viewport so cards must wrap
        # instead of overflowing past the (hidden) horizontal scrollbar.
        super().resizeEvent(e)
        vp = self.viewport()
        if vp is not None:
            self._container.setMaximumWidth(vp.width())

    def set_recordings(
        self,
        rows: Iterable[tuple[Recording, str | None, int, int]],
    ) -> None:
        """Replace the list. Each item is (Recording, one_line, todo_count, todos_done)."""
        self._clear()

        rows_list = list(rows)
        now = datetime.now().astimezone()
        groups: dict[str, list[tuple[Recording, str | None, int, int]]] = {}
        for row in rows_list:
            groups.setdefault(_bucket_label(row[0].started_at, now), []).append(row)

        order = ["Today", "Yesterday", "This week", "Earlier"]
        for label in order:
            items = groups.get(label, [])
            if not items:
                continue
            header = QLabel(label)
            header.setProperty("role", "muted")
            header.setStyleSheet("font-weight: 600; padding-top: 4px;")
            self._layout.insertWidget(self._layout.count() - 1, header)
            for rec, one_line, todo_count, todos_done in items:
                assert rec.id is not None
                card = MeetingCard(rec, one_line=one_line, todo_count=todo_count, todos_done=todos_done)
                card.clicked.connect(self._on_card_clicked)
                self._cards[rec.id] = card
                self._layout.insertWidget(self._layout.count() - 1, card)

        # Re-apply selection (if the previously selected card still exists).
        if self._selected_id is not None and self._selected_id in self._cards:
            self._cards[self._selected_id].set_selected(True)

    def select(self, recording_id: int) -> None:
        """Programmatically select a card and emit the selection signal."""
        self._apply_selection(recording_id)
        self.recording_selected.emit(recording_id)

    def _on_card_clicked(self, recording_id: int) -> None:
        self._apply_selection(recording_id)
        self.recording_selected.emit(recording_id)

    def _apply_selection(self, recording_id: int) -> None:
        if self._selected_id is not None and self._selected_id in self._cards:
            self._cards[self._selected_id].set_selected(False)
        self._selected_id = recording_id
        if recording_id in self._cards:
            self._cards[recording_id].set_selected(True)

    def _clear(self) -> None:
        self._cards.clear()
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()


def _bucket_label(started_at: str, now: datetime) -> str:
    try:
        dt = datetime.fromisoformat(started_at).astimezone()
    except ValueError:
        return "Earlier"
    delta = now - dt
    if dt.date() == now.date():
        return "Today"
    if (now.date() - dt.date()) == timedelta(days=1):
        return "Yesterday"
    if delta <= timedelta(days=7):
        return "This week"
    return "Earlier"


def filter_for_bucket(
    rows: list[tuple[Recording, str | None, int, int]],
    bucket: SidebarBucket,
) -> list[tuple[Recording, str | None, int, int]]:
    """Apply sidebar filtering on top of the day-bucket grouping."""
    if bucket == SidebarBucket.ALL:
        return rows
    if bucket == SidebarBucket.MANUAL:
        return [r for r in rows if r[0].source.value == "manual"]
    if bucket == SidebarBucket.FAILED:
        return [r for r in rows if "failed" in r[0].status.value]
    now = datetime.now().astimezone()
    label_map = {
        SidebarBucket.TODAY: "Today",
        SidebarBucket.YESTERDAY: "Yesterday",
        SidebarBucket.THIS_WEEK: "This week",
        SidebarBucket.EARLIER: "Earlier",
    }
    target = label_map[bucket]
    return [r for r in rows if _bucket_label(r[0].started_at, now) == target]
