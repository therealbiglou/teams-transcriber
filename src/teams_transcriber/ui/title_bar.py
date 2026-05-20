"""Custom title bar for a frameless window."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from teams_transcriber.ui.icons import IconName, get_icon


class TitleBar(QWidget):
    """A simple draggable title bar with min/max/close buttons."""

    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setObjectName("TitleBar")
        self._drag_anchor: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(8)

        self.title_label = QLabel("Teams Transcriber")
        self.title_label.setProperty("role", "subtitle")
        layout.addWidget(self.title_label)
        layout.addStretch(1)

        self.settings_btn = self._make_btn(IconName.SETTINGS, self.settings_requested.emit)
        layout.addWidget(self.settings_btn)

        self.minimize_btn = self._make_btn(IconName.MINIMIZE, self.minimize_requested.emit)
        self.maximize_btn = self._make_btn(IconName.MAXIMIZE, self.maximize_requested.emit)
        self.close_btn = self._make_btn(IconName.CLOSE, self.close_requested.emit)
        layout.addWidget(self.minimize_btn)
        layout.addWidget(self.maximize_btn)
        layout.addWidget(self.close_btn)

    def _make_btn(self, icon: IconName, handler: Callable[[], None]) -> QPushButton:
        btn = QPushButton(get_icon(icon), "")
        btn.setProperty("role", "ghost")
        btn.setFixedSize(32, 28)
        btn.clicked.connect(handler)
        return btn

    def set_maximized(self, maximized: bool) -> None:
        self.maximize_btn.setIcon(get_icon(IconName.RESTORE if maximized else IconName.MAXIMIZE))

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_anchor = e.globalPosition().toPoint() - self.window().pos()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_anchor is not None and not self.window().isMaximized():
            self.window().move(e.globalPosition().toPoint() - self._drag_anchor)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        del e
        self._drag_anchor = None

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        del e
        self.maximize_requested.emit()
