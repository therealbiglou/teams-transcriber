"""Top-level frameless QMainWindow with edge-drag resize and rounded corners."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.theme import app_stylesheet
from teams_transcriber.ui.title_bar import TitleBar


class MainWindow(FramelessWindowMixin, QMainWindow):
    """Frameless window with rounded corners (when not maximized), drag-resize from edges.

    Body is title bar + a compact action row (Record / Import) + a single
    content area (the meeting history list, wired up by ``App``). No
    sidebar, no splitter, no content stack.
    """

    record_requested = Signal()
    import_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.resize(1200, 760)
        self.setMinimumSize(640, 440)

        outer = QFrame()
        outer.setObjectName("OuterFrame")
        outer.setMouseTracking(True)

        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.title_bar = TitleBar(controls=("settings", "min", "max", "close"))
        self.title_bar.minimize_requested.connect(self.showMinimized)
        self.title_bar.maximize_requested.connect(self.toggle_max)
        self.title_bar.close_requested.connect(self.close)
        outer_layout.addWidget(self.title_bar)

        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(24, 16, 24, 0)
        action_layout.setSpacing(8)

        self.record_btn = QPushButton("Record")
        self.record_btn.setProperty("role", "primary")
        self.record_btn.setFixedHeight(36)
        self.record_btn.clicked.connect(self.record_requested)
        action_layout.addWidget(self.record_btn)

        self.import_btn = QPushButton("Import…")
        self.import_btn.setProperty("role", "secondary")
        self.import_btn.setFixedHeight(36)
        self.import_btn.setToolTip(
            "Import an audio file (.opus/.wav/.mp3/.m4a/.flac/.ogg/.mp4) "
            "to transcribe + summarize, OR a transcript file "
            "(.txt/.md/.vtt/.srt) to summarize directly."
        )
        self.import_btn.clicked.connect(self.import_requested)
        action_layout.addWidget(self.import_btn)

        action_layout.addStretch(1)
        outer_layout.addWidget(action_row)

        self.content = QWidget()
        self.content.setObjectName("ContentArea")
        self._content_layout = QVBoxLayout(self.content)
        self._content_layout.setContentsMargins(24, 24, 24, 24)
        self._content_layout.setSpacing(16)
        outer_layout.addWidget(self.content, 1)

        shell_host = QWidget()
        shell_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        shell = QVBoxLayout(shell_host)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.addWidget(outer)
        self.setCentralWidget(shell_host)

        self._init_frameless(outer, resizable=True, title_bar=self.title_bar,
                             shell_layout=shell)

        from teams_transcriber.ui.window_state import restore_window_geometry
        restore_window_geometry(self, "main", default_size=(1200, 760))

    def closeEvent(self, ev) -> None:
        from teams_transcriber.ui.window_state import save_window_geometry
        save_window_geometry(self, "main")
        super().closeEvent(ev)

    def set_recording_active(self, active: bool) -> None:
        """Sync the Record/Stop button label to current recording state."""
        self.record_btn.setText("Stop" if active else "Record")

    def set_content(self, widget: QWidget) -> None:
        """Replace the content area's child widget."""
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._content_layout.addWidget(widget)


def make_app() -> QApplication:
    """Construct a QApplication with the app stylesheet applied."""
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([])
    app.setApplicationName("Teams Transcriber")
    app.setOrganizationName("Teams Transcriber")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(app_stylesheet())
    return app
