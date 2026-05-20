"""Banner widget shown above the history list while a recording is active."""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
)


class ActiveRecordingBanner(QFrame):
    """A pill-shaped banner that shows the in-flight recording.

    Hidden by default. Call `show_recording(rid, title)` when a recording
    starts (or transitions to processing) and `hide_banner()` when it ends.
    The clicked signal carries the recording_id.
    """

    clicked = Signal(int)            # recording_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("activeRecordingBanner")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            "QFrame#activeRecordingBanner { "
            "background: #FFFFFF; border-radius: 12px; "
            "border: 1px solid #E5E7EB; }"
        )
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(12)
        shadow.setColor(QColor(0, 0, 0, 14))
        shadow.setOffset(0, 1)
        self.setGraphicsEffect(shadow)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 12, 10)
        layout.setSpacing(10)

        self._dot = QLabel("●")
        self._dot.setStyleSheet("color: #EF4444; font-size: 16px;")
        layout.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)

        self._title_label = QLabel("")
        self._title_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        self._title_label.setWordWrap(False)
        self._title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._title_label, 1, Qt.AlignmentFlag.AlignVCenter)

        self._elapsed_label = QLabel("00:00")
        self._elapsed_label.setStyleSheet("color: #6B7280; font-size: 12px;")
        self._elapsed_label.setFixedWidth(48)
        layout.addWidget(self._elapsed_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._open_btn = QPushButton("Open workspace")
        self._open_btn.setProperty("role", "secondary")
        self._open_btn.setFixedHeight(28)
        self._open_btn.clicked.connect(self._emit_clicked)
        layout.addWidget(self._open_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._recording_id: int | None = None
        self._started_at: float = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        self.hide()

    def show_recording(self, recording_id: int, title: str, *, status_label: str = "Recording") -> None:
        self._recording_id = recording_id
        self._started_at = time.monotonic()
        self._title_label.setText(f"{status_label}: {title}")
        if status_label == "Recording":
            self._dot.setStyleSheet("color: #EF4444; font-size: 16px;")
        else:
            # Processing: amber dot.
            self._dot.setStyleSheet("color: #F59E0B; font-size: 16px;")
        self._elapsed_label.setText("00:00")
        self._timer.start()
        self.show()

    def set_processing(self) -> None:
        """Transition from RECORDING display to PROCESSING display, keeping the same recording_id."""
        if self._recording_id is None:
            return
        self._dot.setStyleSheet("color: #F59E0B; font-size: 16px;")
        title_text = self._title_label.text()
        if title_text.startswith("Recording:"):
            title_text = "Processing:" + title_text[len("Recording:"):]
            self._title_label.setText(title_text)
        # Stop ticking — the elapsed timer was measuring recording time, not processing time.
        self._timer.stop()
        # Repurpose the time label as a "Processing..." indicator.
        self._elapsed_label.setText("…")
        self._elapsed_label.setFixedWidth(20)

    def hide_banner(self) -> None:
        self._timer.stop()
        self._recording_id = None
        self.hide()

    def current_recording_id(self) -> int | None:
        return self._recording_id

    def _tick(self) -> None:
        elapsed = int(time.monotonic() - self._started_at)
        mm = elapsed // 60
        ss = elapsed % 60
        self._elapsed_label.setText(f"{mm:02d}:{ss:02d}")

    def _emit_clicked(self) -> None:
        if self._recording_id is not None:
            self.clicked.emit(self._recording_id)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._emit_clicked()
            e.accept()
            return
        super().mousePressEvent(e)
