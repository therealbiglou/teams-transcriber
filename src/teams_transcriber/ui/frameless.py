"""Reusable frameless-window chrome: drag-move (via TitleBar), edge resize,
rounded corners that square off when maximized.

Host requirements:
  * be a QWidget subclass with FramelessWindowHint set and
    WA_TranslucentBackground enabled,
  * build an outer QFrame (objectName 'OuterFrame') as its only top-level
    child and call self._init_frameless(outer_frame),
  * give the TitleBar's maximize_requested signal to self.toggle_max and
    minimize/close to showMinimized/close.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QCursor, QMouseEvent

from teams_transcriber.ui.theme import COLORS, RADIUS

_RESIZE_MARGIN: int = 6


class FramelessWindowMixin:
    _outer = None            # type: ignore[var-annotated]
    _resizable: bool = True
    _title_bar = None        # type: ignore[var-annotated]

    def _init_frameless(self, outer, *, resizable: bool = True, title_bar=None) -> None:
        self._outer = outer
        self._resizable = resizable
        self._title_bar = title_bar
        outer.setObjectName("OuterFrame")
        self.setMouseTracking(True)          # type: ignore[attr-defined]
        outer.setMouseTracking(True)
        self._apply_outer_style(maximized=False)

    def _apply_outer_style(self, *, maximized: bool) -> None:
        radius = 0 if maximized else RADIUS["window"]
        self._outer.setStyleSheet(
            f"#OuterFrame {{ background: {COLORS['bg']}; border-radius: {radius}px; }}"
        )

    def toggle_max(self) -> None:
        if self.isMaximized():               # type: ignore[attr-defined]
            self.showNormal()                # type: ignore[attr-defined]
            self._apply_outer_style(maximized=False)
            if self._title_bar is not None:
                self._title_bar.set_maximized(False)
        else:
            self.showMaximized()             # type: ignore[attr-defined]
            self._apply_outer_style(maximized=True)
            if self._title_bar is not None:
                self._title_bar.set_maximized(True)

    def _edge_at(self, pos: QPoint):
        edges = Qt.Edges()
        if not self._resizable or self.isMaximized():   # type: ignore[attr-defined]
            return edges
        rect = self.rect()                               # type: ignore[attr-defined]
        if pos.x() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= rect.width() - _RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= rect.height() - _RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _cursor_for_edges(self, edges) -> Qt.CursorShape:
        if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge) and edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
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
        self.setCursor(QCursor(self._cursor_for_edges(edges)))  # type: ignore[attr-defined]
        super().mouseMoveEvent(e)                               # type: ignore[misc]

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            edges = self._edge_at(e.position().toPoint())
            if edges:
                handle = self.windowHandle()                    # type: ignore[attr-defined]
                if handle is not None:
                    handle.startSystemResize(edges)
                    e.accept()
                    return
        super().mousePressEvent(e)                              # type: ignore[misc]

    def leaveEvent(self, e: QEvent) -> None:
        self.unsetCursor()                                      # type: ignore[attr-defined]
        super().leaveEvent(e)                                   # type: ignore[misc]
