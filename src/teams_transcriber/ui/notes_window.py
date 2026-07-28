"""Capture-only notes window shown while a meeting records.

Replaces the old split notes+live-transcript ``WorkspaceWindow``. Manual
notes are a pure capture channel here: they steer the AI summary's
attribution of owners and become the Wrike project comment, but there is no
live transcript view — that's dropped entirely per the Phase 19 design.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.storage import Database
from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.notes_editor import NotesEditor
from teams_transcriber.ui.title_bar import TitleBar

_WINDOW_STATE_KEY = "notes_window"


class NotesWindow(FramelessWindowMixin, QWidget):
    """Frameless, notes-only window for capturing context during a recording."""

    stop_requested = Signal()
    closed = Signal(int)  # recording_id

    def __init__(
        self,
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
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.resize(640, 760)

        self._frame = QFrame()
        self._frame.setObjectName("OuterFrame")  # mixin styles this

        outer = QVBoxLayout(self)
        outer.addWidget(self._frame)

        inner = QVBoxLayout(self._frame)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        self._title_bar = TitleBar(
            title="Meeting notes",
            controls=("min", "max", "close"),
        )
        self._title_bar.minimize_requested.connect(self.showMinimized)
        self._title_bar.maximize_requested.connect(self.toggle_max)
        self._title_bar.close_requested.connect(self.close)
        inner.addWidget(self._title_bar)

        self.notes_editor = NotesEditor(db, recording_id, parent=self._frame)
        inner.addWidget(self.notes_editor, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(16, 12, 16, 16)
        footer.addStretch(1)
        self.stop_button = QPushButton("Stop recording")
        self.stop_button.setProperty("role", "danger")
        self.stop_button.clicked.connect(self._on_stop_clicked)
        footer.addWidget(self.stop_button)
        inner.addLayout(footer)

        self._init_frameless(
            self._frame, resizable=True, title_bar=self._title_bar, shell_layout=outer,
        )

        from teams_transcriber.ui.window_state import restore_window_geometry
        restore_window_geometry(self, _WINDOW_STATE_KEY, default_size=(640, 760))

    def set_text(self, text: str) -> None:
        """Test/convenience helper: replace the notes body with plain text."""
        self.notes_editor.editor.setPlainText(text)

    def save(self) -> None:
        """Persist the current notes text via ``RecordingRepo.set_manual_notes``."""
        self.notes_editor.flush_now()

    def _on_stop_clicked(self) -> None:
        self.save()
        self.stop_requested.emit()

    def closeEvent(self, ev) -> None:
        from teams_transcriber.ui.window_state import save_window_geometry
        save_window_geometry(self, _WINDOW_STATE_KEY)
        self.save()
        self.closed.emit(self._recording_id)
        super().closeEvent(ev)
