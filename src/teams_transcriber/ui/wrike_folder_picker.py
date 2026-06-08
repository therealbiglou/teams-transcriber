"""Themed modal dialog: pick a Wrike folder for a summary's tasks."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.title_bar import TitleBar


class WrikeFolderPicker(FramelessWindowMixin, QDialog):
    """List recent + all folders, with a search box. Returns selected id."""

    def __init__(
        self,
        *,
        folders: list[dict[str, Any]],
        recent_folder_ids: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick Wrike folder")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(420, 460)
        self.selected_folder_id: str | None = None

        frame = QFrame(); frame.setObjectName("OuterFrame")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0); shell.addWidget(frame)
        inner = QVBoxLayout(frame); inner.setContentsMargins(0, 0, 0, 0); inner.setSpacing(0)

        self._title_bar = TitleBar(title="Pick Wrike folder", controls=("close",))
        self._title_bar.close_requested.connect(self.reject)
        inner.addWidget(self._title_bar)

        body = QWidget()
        v = QVBoxLayout(body); v.setContentsMargins(16, 12, 16, 16); v.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search folders…")
        self._search.textChanged.connect(self._apply_filter)
        v.addWidget(self._search)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _i: self._on_accept())
        v.addWidget(self._list, 1)

        # Populate: recent first, then the rest (deduped).
        recent_set = set(recent_folder_ids)
        ordered = (
            [f for fid in recent_folder_ids for f in folders if f["id"] == fid]
            + [f for f in folders if f["id"] not in recent_set]
        )
        for f in ordered:
            label = f["title"] + ("  ★" if f["id"] in recent_set else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, f["id"])
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

        btn_row = QHBoxLayout(); btn_row.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.setProperty("role", "secondary")
        cancel.clicked.connect(self.reject); btn_row.addWidget(cancel)
        ok = QPushButton("Send"); ok.setProperty("role", "primary"); ok.setDefault(True)
        ok.clicked.connect(self._on_accept); btn_row.addWidget(ok)
        v.addLayout(btn_row)

        inner.addWidget(body, 1)
        self._init_frameless(frame, resizable=True, title_bar=self._title_bar)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _on_accept(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        self.selected_folder_id = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
