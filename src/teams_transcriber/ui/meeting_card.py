"""A single meeting row in the history list."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.storage.models import Recording, RecordingStatus


class MeetingCard(QFrame):
    """Card showing one recording's title, status, duration, one_line."""

    clicked = Signal(int)  # recording_id

    def __init__(
        self,
        recording: Recording,
        one_line: str | None,
        todo_count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        assert recording.id is not None
        self._recording_id = recording.id
        self.setProperty("card", True)
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shadow: QGraphicsDropShadowEffect | None = None

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(12)
        shadow.setColor(QColor(0, 0, 0, 18))
        shadow.setOffset(0, 1)
        self.setGraphicsEffect(shadow)
        self._shadow = shadow

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(8)

        # Top row: title + status chip
        top = QHBoxLayout()
        title_text = recording.display_title or recording.detected_title or "(untitled)"
        title = QLabel(title_text)
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(title, 1)

        chip = _status_chip(recording.status)
        if chip is not None:
            top.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)

        outer.addLayout(top)

        when = _fmt_time(recording.started_at)
        dur = _fmt_duration(recording.duration_ms or 0)
        meta = QLabel(f"{when} · {dur}")
        meta.setProperty("role", "muted")
        meta.setWordWrap(True)
        outer.addWidget(meta)

        if one_line:
            ol = QLabel(one_line)
            ol.setWordWrap(True)
            ol.setStyleSheet("color: #374151; font-size: 13px;")
            outer.addWidget(ol)

        if todo_count > 0:
            footer = QHBoxLayout()
            todo_chip = QLabel(f"{todo_count} todo{'s' if todo_count != 1 else ''}")
            todo_chip.setProperty("role", "chip")
            todo_chip.setProperty("variant", "success")
            style = todo_chip.style()
            if style is not None:
                style.unpolish(todo_chip)
                style.polish(todo_chip)
            footer.addWidget(todo_chip)
            footer.addStretch(1)
            outer.addLayout(footer)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._recording_id)

    def set_selected(self, selected: bool) -> None:
        """Toggle the visual 'selected' state — stronger shadow + colored left edge via QSS."""
        self.setProperty("selected", selected)
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        if self._shadow is not None:
            if selected:
                self._shadow.setBlurRadius(20)
                self._shadow.setColor(QColor(16, 185, 129, 64))  # emerald-tinted
                self._shadow.setOffset(0, 2)
            else:
                self._shadow.setBlurRadius(12)
                self._shadow.setColor(QColor(0, 0, 0, 18))
                self._shadow.setOffset(0, 1)


def _status_chip(status: RecordingStatus) -> QLabel | None:
    label_variant: dict[RecordingStatus, tuple[str, str]] = {
        RecordingStatus.RECORDING:           ("Recording", "warn"),
        RecordingStatus.TRANSCRIBING:        ("Transcribing", "warn"),
        RecordingStatus.SUMMARIZING:         ("Summarizing", "warn"),
        RecordingStatus.DONE:                ("", ""),
        RecordingStatus.RECORDING_FAILED:    ("Failed", "error"),
        RecordingStatus.TRANSCRIPTION_FAILED: ("Failed", "error"),
        RecordingStatus.SUMMARY_FAILED:      ("Failed", "error"),
    }
    text, variant = label_variant.get(status, ("", ""))
    if not text:
        return None
    chip = QLabel(text)
    chip.setProperty("role", "chip")
    chip.setProperty("variant", variant)
    return chip


def _fmt_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso).astimezone()  # convert UTC → local
        return dt.strftime("%b %d, %I:%M %p").lstrip("0").replace(" 0", " ")
    except ValueError:
        return iso


def _fmt_duration(ms: int) -> str:
    total_s = ms // 1000
    if total_s < 60:
        return f"{total_s}s"
    m, _s = divmod(total_s, 60)
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"
