"""Left sidebar with date-bucket filter buttons."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget


# Section headings ("Meeting History", "Todos") — deliberately prominent so they
# read as headings, not menu items. (Qt QSS supports font-size/weight/color but
# not text-transform/letter-spacing, so prominence comes from size + weight + a
# darker color + a divider rule.)
_HEADING_STYLE = (
    "font-size: 15px; font-weight: 800; color: #111827; "
    "border-bottom: 1px solid #E5E7EB; margin-bottom: 2px; "
)


class SidebarBucket(Enum):
    ALL = "All meetings"
    TODAY = "Today"
    YESTERDAY = "Yesterday"
    THIS_WEEK = "This week"
    EARLIER = "Earlier"
    MANUAL = "Manual recordings"
    FAILED = "Failed"


class Sidebar(QFrame):
    """Sidebar with one button per SidebarBucket."""

    bucket_selected = Signal(SidebarBucket)
    todos_selected = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "sidebar")
        self.setMinimumWidth(150)
        self.setMaximumWidth(340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 24, 16, 24)
        layout.setSpacing(4)

        header = QLabel("Meeting History")
        header.setStyleSheet(_HEADING_STYLE + "padding: 0 8px 8px 8px;")
        layout.addWidget(header)

        self._buttons: dict[SidebarBucket, QPushButton] = {}
        for bucket in SidebarBucket:
            btn = QPushButton(bucket.value)
            btn.setProperty("sidebar_item", True)
            btn.clicked.connect(lambda _checked=False, b=bucket: self._select(b))
            layout.addWidget(btn)
            self._buttons[bucket] = btn

        todos_header = QLabel("Todos")
        todos_header.setStyleSheet(_HEADING_STYLE + "padding: 20px 8px 8px 8px;")
        layout.addWidget(todos_header)

        self.todos_button = QPushButton("To-Do List")
        self.todos_button.setProperty("sidebar_item", True)
        self.todos_button.clicked.connect(self._select_todos)
        layout.addWidget(self.todos_button)

        layout.addStretch(1)

        self._active: SidebarBucket = SidebarBucket.ALL
        self._active_is_todos: bool = False
        self._refresh_active()

    def _select(self, bucket: SidebarBucket) -> None:
        self._active = bucket
        self._active_is_todos = False
        self._refresh_active()
        self.bucket_selected.emit(bucket)

    def _select_todos(self) -> None:
        self._active_is_todos = True
        self._refresh_active()
        self.todos_selected.emit()

    def select_bucket(self, bucket: SidebarBucket) -> None:
        """Programmatically select a History bucket (used by 'Go to summary')."""
        self._select(bucket)

    def _refresh_active(self) -> None:
        for bucket, btn in self._buttons.items():
            btn.setProperty("active", (not self._active_is_todos) and bucket == self._active)
            self._restyle(btn)
        if hasattr(self, "todos_button"):
            self.todos_button.setProperty("active", self._active_is_todos)
            self._restyle(self.todos_button)

    @staticmethod
    def _restyle(btn) -> None:
        style = btn.style()
        if style is not None:
            style.unpolish(btn)
            style.polish(btn)

    @property
    def active_bucket(self) -> SidebarBucket:
        return self._active
