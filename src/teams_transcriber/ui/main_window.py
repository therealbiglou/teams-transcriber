"""Top-level frameless QMainWindow with edge-drag resize and rounded corners."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.sidebar import Sidebar
from teams_transcriber.ui.theme import app_stylesheet
from teams_transcriber.ui.title_bar import TitleBar


class MainWindow(FramelessWindowMixin, QMainWindow):
    """Frameless window with rounded corners (when not maximized), drag-resize from edges."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.resize(1200, 760)
        self.setMinimumSize(900, 540)

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

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.sidebar = Sidebar()
        body_layout.addWidget(self.sidebar)

        self.content = QWidget()
        self.content.setObjectName("ContentArea")
        self._content_layout = QVBoxLayout(self.content)
        self._content_layout.setContentsMargins(24, 24, 24, 24)
        self._content_layout.setSpacing(16)
        body_layout.addWidget(self.content, 1)

        outer_layout.addWidget(body, 1)

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

    def closeEvent(self, ev) -> None:  # noqa: N802
        from teams_transcriber.ui.window_state import save_window_geometry
        save_window_geometry(self, "main")
        super().closeEvent(ev)

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
