"""Transcript viewer — channel-labeled segments with timestamps."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from teams_transcriber.storage import Database, TranscriptRepo
from teams_transcriber.storage.models import Channel
from teams_transcriber.ui.theme import COLORS


class TranscriptView(QScrollArea):
    """Scrollable list of transcript segments."""

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = db
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._layout.addStretch(1)
        self.setWidget(self._container)

    def show_recording(self, recording_id: int) -> None:
        self._clear()
        segments = TranscriptRepo(self._db).list_for_recording(recording_id)
        for seg in segments:
            self._layout.insertWidget(
                self._layout.count() - 1,
                _make_row(seg.channel, seg.start_ms, seg.text),
            )

    def _clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()


def _make_row(channel: Channel, start_ms: int, text: str) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(4, 2, 4, 2)
    layout.setSpacing(8)

    ts = QLabel(_format_ms(start_ms))
    ts.setFixedWidth(60)
    ts.setProperty("role", "hint")

    who = QLabel("ME" if channel == Channel.ME else "OTHER")
    who.setFixedWidth(56)
    who.setStyleSheet(
        f"color: {COLORS['accent_active'] if channel == Channel.ME else COLORS['text_secondary']};"
        "font-weight: 600; font-size: 12px;"
    )

    body = QLabel(text)
    body.setWordWrap(True)

    layout.addWidget(ts)
    layout.addWidget(who)
    layout.addWidget(body, 1)
    return row


def _format_ms(ms: int) -> str:
    total_s = ms // 1000
    m, s = divmod(total_s, 60)
    return f"{m:02d}:{s:02d}"
