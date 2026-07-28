"""Scrollable list of MeetingRows grouped by date bucket headers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from teams_transcriber.storage.models import Recording
from teams_transcriber.ui.meeting_row import MeetingRow
from teams_transcriber.ui.meeting_status import RowState


class HistoryList(QScrollArea):
    """List of MeetingRows with optional date-bucket headers."""

    action_requested = Signal(int, str)   # recording_id, RowAction value
    delete_requested = Signal(int)        # recording_id

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

    def resizeEvent(self, e: QResizeEvent) -> None:
        # Guard #2: pin the container to the viewport so rows must wrap
        # instead of overflowing past the (hidden) horizontal scrollbar.
        super().resizeEvent(e)
        vp = self.viewport()
        if vp is not None:
            self._container.setMaximumWidth(vp.width())

    def set_rows(self, rows: Iterable[tuple[Recording, RowState]]) -> None:
        """Replace the list. Each item is (Recording, RowState)."""
        self._clear()
        rows_list = list(rows)
        now = datetime.now().astimezone()
        groups: dict[str, list[tuple[Recording, RowState]]] = {}
        for rec, state in rows_list:
            groups.setdefault(_bucket_label(rec.started_at, now), []).append((rec, state))

        for label in ("Today", "Yesterday", "This week", "Earlier"):
            items = groups.get(label, [])
            if not items:
                continue
            header = QLabel(label)
            header.setProperty("role", "muted")
            header.setStyleSheet("font-weight: 600; padding-top: 4px;")
            self._layout.insertWidget(self._layout.count() - 1, header)
            for rec, state in items:
                row = MeetingRow(rec, state)
                row.action_requested.connect(self.action_requested)
                row.delete_requested.connect(self.delete_requested)
                self._layout.insertWidget(self._layout.count() - 1, row)

    def _clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.setParent(None)
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
