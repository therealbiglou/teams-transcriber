"""Live workspace window: notes (70 %) + live transcript (30 %).

Frameless themed window matching the MainWindow's visual language. Live
mode subscribes to LiveSegmentAvailable via the bridge and appends each
segment to the transcript view. Past-recording mode loads segments once
and doesn't subscribe to updates.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.events import LiveSegmentAvailable
from teams_transcriber.storage import Database, RecordingRepo, TranscriptRepo
from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.live_transcript_view import LiveTranscriptView
from teams_transcriber.ui.notes_editor import NotesEditor
from teams_transcriber.ui.qt_bridge import QtEventBridge
from teams_transcriber.ui.title_bar import TitleBar


class WorkspaceWindow(FramelessWindowMixin, QWidget):
    """Frameless workspace window with notes (70 %) + live transcript (30 %)."""

    stop_recording_requested = Signal(int)  # recording_id
    closed = Signal(int)                    # recording_id

    def __init__(
        self,
        *,
        db: Database,
        recording_id: int,
        bridge: QtEventBridge,
        live: bool,
        settings=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._recording_id = recording_id
        self._bridge = bridge
        self._live = live
        self._settings = settings
        self._placeholder = None

        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.resize(1100, 700)

        self._frame = QFrame()
        self._frame.setObjectName("OuterFrame")  # mixin styles this

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._frame)

        inner = QVBoxLayout(self._frame)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        rec = RecordingRepo(db).get(recording_id)
        title = (rec.display_title if rec else None) or "Meeting"

        self._dot = QLabel("●")
        self._dot.setStyleSheet("color: #9CA3AF; font-size: 14px;")

        self._pin_btn = QPushButton("📌")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setProperty("role", "ghost")
        self._pin_btn.setFixedSize(28, 28)
        self._pin_btn.setToolTip("Always on top")
        self._pin_btn.toggled.connect(self._on_always_on_top)

        self._title_bar = TitleBar(
            title=title,
            controls=("min", "max", "close"),
            extra_left=[self._dot],
            extra_right=[self._pin_btn],
        )
        self._title_bar.minimize_requested.connect(self.showMinimized)
        self._title_bar.maximize_requested.connect(self.toggle_max)
        self._title_bar.close_requested.connect(self.close)
        inner.addWidget(self._title_bar)

        self._set_recording_dot(live)

        # 70/30 splitter
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(8)
        self.notes_editor = NotesEditor(db, recording_id, parent=self._splitter)
        self.transcript_view = LiveTranscriptView(self._splitter)
        self._splitter.addWidget(self.notes_editor)
        self._splitter.addWidget(self.transcript_view)
        self._splitter.setSizes([700, 300])
        self._splitter.setStretchFactor(0, 7)
        self._splitter.setStretchFactor(1, 3)
        inner.addWidget(self._splitter, 1)

        # Footer
        footer = QHBoxLayout()
        footer.setContentsMargins(16, 12, 16, 16)
        self._waiting_label = QLabel("")
        self._waiting_label.setStyleSheet("color: #B45309; font-size: 12px;")
        footer.addWidget(self._waiting_label)
        footer.addStretch(1)
        self._stop_button = QPushButton("Stop recording")
        self._stop_button.setProperty("role", "danger")
        self._stop_button.clicked.connect(
            lambda: self.stop_recording_requested.emit(self._recording_id),
        )
        self._stop_button.setVisible(live)
        footer.addWidget(self._stop_button)
        close_btn = QPushButton("Close")
        close_btn.setProperty("role", "secondary")
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        inner.addLayout(footer)

        # Wire live or past mode.
        live_streaming_enabled = (
            settings is None or settings.transcription_live_enabled
        )
        if live and live_streaming_enabled:
            self._bridge.live_segment_available.connect(self._on_live_segment)
        elif live and not live_streaming_enabled:
            # Phase 6: live disabled — show placeholder, reload on SummaryReady.
            self._show_placeholder(
                "Transcription will appear when the meeting ends."
            )
            self._bridge.summary_ready.connect(self._on_summary_ready_refresh)
        else:
            segments = TranscriptRepo(db).list_for_recording(recording_id)
            self.transcript_view.load_segments(segments)

        self._init_frameless(self._frame, resizable=True,
                             title_bar=self._title_bar, shell_layout=outer)

    def _set_recording_dot(self, recording: bool) -> None:
        color = "#EF4444" if recording else "#9CA3AF"
        self._dot.setStyleSheet(f"color: {color}; font-size: 14px;")

    def _show_placeholder(self, text: str) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel
        placeholder = QLabel(text)
        placeholder.setStyleSheet("color: #6B7280; padding: 24px; font-size: 13px;")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Insert above the transcript_view in its parent layout.
        parent_widget = self.transcript_view.parentWidget()
        parent_layout = parent_widget.layout() if parent_widget else None
        if parent_layout is not None:
            parent_layout.insertWidget(0, placeholder)
        self._placeholder = placeholder

    def _on_summary_ready_refresh(self, evt) -> None:
        if evt.recording_id != self._recording_id:
            return
        from teams_transcriber.storage import TranscriptRepo
        segments = TranscriptRepo(self._db).list_for_recording(self._recording_id)
        self.transcript_view.load_segments(segments)
        if hasattr(self, "_placeholder") and self._placeholder is not None:
            self._placeholder.deleteLater()
            self._placeholder = None

    def _on_live_segment(self, evt: LiveSegmentAvailable) -> None:
        if evt.recording_id != self._recording_id:
            return
        self.transcript_view.append_segment(evt.segment)

    def _on_always_on_top(self, enabled: bool) -> None:
        flags = self.windowFlags()
        if enabled:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        self.show()

    def show_waiting_for_processing(self) -> None:
        """Indicate that transcription/summarization is paused until close."""
        self._waiting_label.setText("⏳ Transcription will start when you close this window.")

    def set_recording_finished(self) -> None:
        """Transition the workspace from live to finished mode."""
        self._set_recording_dot(False)
        self._stop_button.setVisible(False)
        self._live = False
        try:
            self._bridge.live_segment_available.disconnect(self._on_live_segment)
        except (TypeError, RuntimeError):
            pass

    def closeEvent(self, ev) -> None:  # noqa: N802
        self.notes_editor.flush_now()
        try:
            self._bridge.live_segment_available.disconnect(self._on_live_segment)
        except (TypeError, RuntimeError):
            pass
        self.closed.emit(self._recording_id)
        super().closeEvent(ev)
