"""Reusable frameless-window chrome: drag-move (via TitleBar), edge resize,
rounded corners that square off when maximized.

Host requirements:
  * be a QWidget subclass with FramelessWindowHint set and
    WA_TranslucentBackground enabled,
  * build an outer QFrame (objectName 'OuterFrame') as its only top-level
    child and call self._init_frameless(outer_frame),
  * give the TitleBar's maximize_requested signal to self.toggle_max and
    minimize/close to showMinimized/close.
  * to opt into the shell layout (transparent shadow/resize margin band
    around OuterFrame), pass the layout that hosts `outer` as
    `shell_layout=` to `_init_frameless`.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QCursor, QMouseEvent
from PySide6.QtWidgets import QGraphicsDropShadowEffect

from teams_transcriber.ui.theme import COLORS, RADIUS

CHROME_MARGIN: int = 18   # transparent band around OuterFrame: shadow + resize zone
_RESIZE_MARGIN: int = 6   # legacy band when no shell layout is managed
_EDGE_INSET: int = 4      # resize band also extends this far inside the frame


class FramelessWindowMixin:
    _outer = None            # type: ignore[var-annotated]
    _resizable: bool = True
    _title_bar = None        # type: ignore[var-annotated]
    _shell_layout = None     # type: ignore[var-annotated]
    _shadow = None           # type: ignore[var-annotated]

    def _init_frameless(
        self, outer, *, resizable: bool = True, title_bar=None, shell_layout=None,
    ) -> None:
        self._outer = outer
        self._resizable = resizable
        self._title_bar = title_bar
        self._shell_layout = shell_layout
        outer.setObjectName("OuterFrame")
        self.setMouseTracking(True)          # type: ignore[attr-defined]
        outer.setMouseTracking(True)
        if shell_layout is not None:
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(24)
            shadow.setOffset(0, 2)
            outer.setGraphicsEffect(shadow)
            self._shadow = shadow
        self._apply_chrome()

    def _apply_chrome(self) -> None:
        """Restyle for the current maximized + active state.

        Depth cues: the window shadow and border are stronger when the window
        is active, so the foreground window is visually distinct.
        """
        maximized = self.isMaximized()       # type: ignore[attr-defined]
        active = self.isActiveWindow()       # type: ignore[attr-defined]
        radius = 0 if maximized else RADIUS["window"]
        border = COLORS["border"] if active else COLORS["border_soft"]
        self._outer.setStyleSheet(
            f"#OuterFrame {{ background: {COLORS['bg']}; "
            f"border-radius: {radius}px; border: 1px solid {border}; }}"
        )
        if self._shell_layout is not None:
            m = 0 if maximized else CHROME_MARGIN
            self._shell_layout.setContentsMargins(m, m, m, m)
        if self._shadow is not None:
            self._shadow.setEnabled(not maximized)
            self._shadow.setColor(QColor(0, 0, 0, 90 if active else 40))
        if self._title_bar is not None and hasattr(self._title_bar, "set_window_active"):
            self._title_bar.set_window_active(active)

    # Back-compat shim: toggle_max used to call this.
    def _apply_outer_style(self, *, maximized: bool) -> None:
        del maximized
        self._apply_chrome()

    def toggle_max(self) -> None:
        if self.isMaximized():               # type: ignore[attr-defined]
            self.showNormal()                # type: ignore[attr-defined]
            if self._title_bar is not None:
                self._title_bar.set_maximized(False)
        else:
            self.showMaximized()             # type: ignore[attr-defined]
            if self._title_bar is not None:
                self._title_bar.set_maximized(True)
        self._apply_chrome()

    def changeEvent(self, e: QEvent) -> None:
        # Activation + window-state changes drive the depth styling. Guard on
        # _outer: changeEvent can fire during __init__ before _init_frameless.
        if (
            e.type() in (QEvent.Type.ActivationChange, QEvent.Type.WindowStateChange)
            and self._outer is not None
        ):
            self._apply_chrome()
            if e.type() == QEvent.Type.WindowStateChange and self._title_bar is not None:
                self._title_bar.set_maximized(self.isMaximized())  # type: ignore[attr-defined]
        super().changeEvent(e)                                     # type: ignore[misc]

    def _resize_band(self) -> int:
        if self._shell_layout is not None:
            return CHROME_MARGIN + _EDGE_INSET
        return _RESIZE_MARGIN

    def _edge_at(self, pos: QPoint):
        edges = Qt.Edges()
        if not self._resizable or self.isMaximized():   # type: ignore[attr-defined]
            return edges
        band = self._resize_band()
        rect = self.rect()                               # type: ignore[attr-defined]
        if pos.x() <= band:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= rect.width() - band:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= band:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= rect.height() - band:
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
