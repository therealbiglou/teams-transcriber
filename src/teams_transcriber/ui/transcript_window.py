"""Standalone read-only transcript viewer for a completed recording."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.storage import Database, RecordingRepo, TranscriptRepo
from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.live_transcript_view import LiveTranscriptView
from teams_transcriber.ui.title_bar import TitleBar


class TranscriptWindow(FramelessWindowMixin, QWidget):
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

        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.resize(720, 600)

        frame = QFrame()
        frame.setObjectName("OuterFrame")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        inner = QVBoxLayout(frame)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        rec = RecordingRepo(db).get(recording_id)
        title_text = (rec.display_title if rec else None) or "Transcript"
        self._title_bar = TitleBar(title=title_text, controls=("min", "max", "close"))
        self._title_bar.minimize_requested.connect(self.showMinimized)
        self._title_bar.maximize_requested.connect(self.toggle_max)
        self._title_bar.close_requested.connect(self.close)
        inner.addWidget(self._title_bar)

        body = QVBoxLayout()
        body.setContentsMargins(16, 8, 16, 16)
        self.transcript_view = LiveTranscriptView()
        self.transcript_view.load_segments(TranscriptRepo(db).list_for_recording(recording_id))
        body.addWidget(self.transcript_view, 1)
        inner.addLayout(body)

        self._init_frameless(frame, resizable=True, title_bar=self._title_bar)
