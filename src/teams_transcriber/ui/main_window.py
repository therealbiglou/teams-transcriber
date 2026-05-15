"""Top-level frameless QMainWindow with edge-drag resize and rounded corners."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.ui.sidebar import Sidebar
from teams_transcriber.ui.theme import COLORS, RADIUS, app_stylesheet
from teams_transcriber.ui.title_bar import TitleBar

_RESIZE_MARGIN: int = 6  # pixels of edge that trigger resize


class MainWindow(QMainWindow):
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
        self._outer = outer
        self._apply_outer_style(maximized=False)

        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.title_bar = TitleBar()
        self.title_bar.minimize_requested.connect(self.showMinimized)
        self.title_bar.maximize_requested.connect(self._toggle_max)
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
        self.setCentralWidget(outer)

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

    def _apply_outer_style(self, *, maximized: bool) -> None:
        radius = 0 if maximized else RADIUS["window"]
        self._outer.setStyleSheet(
            f"#OuterFrame {{ background: {COLORS['bg']}; border-radius: {radius}px; }}"
        )

    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self.title_bar.set_maximized(False)
            self._apply_outer_style(maximized=False)
        else:
            self.showMaximized()
            self.title_bar.set_maximized(True)
            self._apply_outer_style(maximized=True)

    # --- edge-drag resize -------------------------------------------------

    def _edge_at(self, pos: QPoint) -> Qt.Edges:  # type: ignore[name-defined]
        """Return which edges the position is near (for cursor / resize)."""
        edges = Qt.Edges()  # type: ignore[attr-defined]
        if self.isMaximized():
            return edges
        rect = self.rect()
        if pos.x() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= rect.width() - _RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= rect.height() - _RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _cursor_for_edges(self, edges: Qt.Edges) -> Qt.CursorShape:  # type: ignore[name-defined]
        if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge) and edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            # corners
            if (edges & Qt.Edge.LeftEdge and edges & Qt.Edge.TopEdge) or \
               (edges & Qt.Edge.RightEdge and edges & Qt.Edge.BottomEdge):
                return Qt.CursorShape.SizeFDiagCursor
            return Qt.CursorShape.SizeBDiagCursor
        if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            return Qt.CursorShape.SizeHorCursor
        if edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        edges = self._edge_at(e.position().toPoint())
        self.setCursor(QCursor(self._cursor_for_edges(edges)))
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            edges = self._edge_at(e.position().toPoint())
            if edges:
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemResize(edges)
                    e.accept()
                    return
        super().mousePressEvent(e)

    def leaveEvent(self, e: QEvent) -> None:
        self.unsetCursor()
        super().leaveEvent(e)


def make_app() -> QApplication:
    """Construct a QApplication with the app stylesheet applied."""
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([])
    app.setApplicationName("Teams Transcriber")
    app.setOrganizationName("Teams Transcriber")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(app_stylesheet())
    return app
