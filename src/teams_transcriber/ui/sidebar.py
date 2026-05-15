"""Left sidebar with date-bucket filter buttons."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "sidebar")
        self.setFixedWidth(220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 24, 16, 24)
        layout.setSpacing(4)

        header = QLabel("History")
        header.setProperty("role", "muted")
        header.setStyleSheet("font-weight: 600; padding: 0 8px 12px 8px;")
        layout.addWidget(header)

        self._buttons: dict[SidebarBucket, QPushButton] = {}
        for bucket in SidebarBucket:
            btn = QPushButton(bucket.value)
            btn.setProperty("sidebar_item", True)
            btn.clicked.connect(lambda _checked=False, b=bucket: self._select(b))
            layout.addWidget(btn)
            self._buttons[bucket] = btn

        layout.addStretch(1)

        self._active: SidebarBucket = SidebarBucket.ALL
        self._refresh_active()

    def _select(self, bucket: SidebarBucket) -> None:
        self._active = bucket
        self._refresh_active()
        self.bucket_selected.emit(bucket)

    def _refresh_active(self) -> None:
        for bucket, btn in self._buttons.items():
            btn.setProperty("active", bucket == self._active)
            style = btn.style()
            if style is not None:
                style.unpolish(btn)
                style.polish(btn)

    @property
    def active_bucket(self) -> SidebarBucket:
        return self._active
