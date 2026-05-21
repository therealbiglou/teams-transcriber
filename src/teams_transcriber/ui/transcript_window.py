"""Standalone read-only transcript viewer for a completed recording."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.storage import Database, RecordingRepo, TranscriptRepo
from teams_transcriber.ui.live_transcript_view import LiveTranscriptView


class TranscriptWindow(QWidget):
    """Frameless themed window showing one recording's full transcript."""

    def __init__(
        self,
        *,
        db: Database,
        recording_id: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._recording_id = recording_id

        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.resize(720, 600)

        frame = QFrame()
        frame.setObjectName("transcriptFrame")
        frame.setStyleSheet(
            "QFrame#transcriptFrame { background: #F2EFE9; border-radius: 16px; }"
        )
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(36)
        shadow.setColor(QColor(0, 0, 0, 60))
        shadow.setOffset(0, 6)
        frame.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.addWidget(frame)

        inner = QVBoxLayout(frame)
        inner.setContentsMargins(16, 12, 16, 16)
        inner.setSpacing(10)

        # Title row
        rec = RecordingRepo(db).get(recording_id)
        title_text = (rec.display_title if rec else None) or "Transcript"
        title_row = QHBoxLayout()
        title = QLabel(title_text)
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        title_row.addWidget(title, 1)
        close = QPushButton("✕")
        close.setProperty("role", "ghost")
        close.setFixedSize(28, 28)
        close.clicked.connect(self.close)
        title_row.addWidget(close)
        inner.addLayout(title_row)

        # Transcript pane
        self.transcript_view = LiveTranscriptView()
        segments = TranscriptRepo(db).list_for_recording(recording_id)
        self.transcript_view.load_segments(segments)
        inner.addWidget(self.transcript_view, 1)
