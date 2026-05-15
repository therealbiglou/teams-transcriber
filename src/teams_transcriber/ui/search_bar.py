"""Top-bar search field with debounced text-changed signal."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget


class SearchBar(QWidget):
    query_changed = Signal(str)  # emitted ~250ms after typing stops

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.input = QLineEdit()
        self.input.setProperty("role", "search")
        self.input.setPlaceholderText("Search transcripts and summaries…")
        self.input.setMinimumHeight(36)
        layout.addWidget(self.input)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.query_changed.emit(self.input.text()))
        self.input.textChanged.connect(lambda _t: self._timer.start(250))
