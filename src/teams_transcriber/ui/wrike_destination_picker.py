"""Themed modal dialog: pick a Wrike destination (a Space, or a folder
within it) for project export."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.title_bar import TitleBar

_SPACE_ROOT_LABEL = "(space root)"


class WrikeDestinationPicker(FramelessWindowMixin, QDialog):
    """Two lists side by side: Spaces (left), targets within the chosen
    Space (right) — the Space itself plus its folders. Returns
    ``(parent_id, label)`` via ``.selected``.
    """

    def __init__(
        self,
        *,
        spaces: list[dict[str, Any]],
        folders_by_space: dict[str, list[dict[str, Any]]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose Wrike destination")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(560, 460)

        self._spaces = spaces
        self._folders_by_space = folders_by_space
        self._current_space_id: str | None = None
        self._current_space_title: str | None = None
        self.selected: tuple[str, str] | None = None

        frame = QFrame(); frame.setObjectName("OuterFrame")
        shell = QVBoxLayout(self)
        shell.addWidget(frame)
        inner = QVBoxLayout(frame); inner.setContentsMargins(0, 0, 0, 0); inner.setSpacing(0)

        self._title_bar = TitleBar(title="Choose Wrike destination", controls=("close",))
        self._title_bar.close_requested.connect(self.reject)
        inner.addWidget(self._title_bar)

        body = QWidget()
        v = QVBoxLayout(body); v.setContentsMargins(16, 12, 16, 16); v.setSpacing(8)

        lists_row = QHBoxLayout(); lists_row.setSpacing(12)

        space_col = QVBoxLayout(); space_col.setSpacing(4)
        space_col.addWidget(QLabel("Space"))
        self._space_list = QListWidget()
        self._space_list.currentItemChanged.connect(self._on_space_item_changed)
        space_col.addWidget(self._space_list, 1)
        lists_row.addLayout(space_col, 1)

        target_col = QVBoxLayout(); target_col.setSpacing(4)
        target_col.addWidget(QLabel("Destination"))
        self._target_list = QListWidget()
        self._target_list.currentItemChanged.connect(self._on_target_item_changed)
        target_col.addWidget(self._target_list, 1)
        lists_row.addLayout(target_col, 1)

        v.addLayout(lists_row, 1)

        for space in self._spaces:
            item = QListWidgetItem(space["title"])
            item.setData(Qt.ItemDataRole.UserRole, space["id"])
            self._space_list.addItem(item)
        if self._space_list.count() > 0:
            self._space_list.setCurrentRow(0)

        btn_row = QHBoxLayout(); btn_row.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.setProperty("role", "secondary")
        cancel.clicked.connect(self.reject); btn_row.addWidget(cancel)
        choose = QPushButton("Choose"); choose.setProperty("role", "primary"); choose.setDefault(True)
        choose.clicked.connect(self._on_accept); btn_row.addWidget(choose)
        v.addLayout(btn_row)

        inner.addWidget(body, 1)
        self._init_frameless(frame, resizable=True, title_bar=self._title_bar,
                             shell_layout=shell)

    def _on_space_item_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        self._select_space(current.data(Qt.ItemDataRole.UserRole))

    def _on_target_item_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        self._select_target(current.data(Qt.ItemDataRole.UserRole))

    def _select_space(self, space_id: str) -> None:
        """Populate the target list for ``space_id``: "(space root)" first,
        then that space's folders. Also selects the space root by default."""
        space = next((s for s in self._spaces if s["id"] == space_id), None)
        self._current_space_id = space_id
        self._current_space_title = space["title"] if space is not None else space_id

        self._target_list.blockSignals(True)
        self._target_list.clear()

        root_item = QListWidgetItem(_SPACE_ROOT_LABEL)
        root_item.setData(Qt.ItemDataRole.UserRole, None)
        self._target_list.addItem(root_item)

        for folder in self._folders_by_space.get(space_id, []):
            item = QListWidgetItem(folder["title"])
            item.setData(Qt.ItemDataRole.UserRole, folder["id"])
            self._target_list.addItem(item)

        self._target_list.blockSignals(False)
        self._target_list.setCurrentRow(0)

    def _select_target(self, target_id: str | None) -> None:
        """Set ``self.selected`` for the chosen target within the current
        space: the space root when ``target_id`` is None, else a folder."""
        space_id = self._current_space_id
        space_title = self._current_space_title
        if space_id is None or space_title is None:
            return
        if target_id is None:
            self.selected = (space_id, space_title)
            return
        folder = next(
            (f for f in self._folders_by_space.get(space_id, []) if f["id"] == target_id),
            None,
        )
        folder_title = folder["title"] if folder is not None else target_id
        self.selected = (target_id, f"{space_title} / {folder_title}")

    def _on_accept(self) -> None:
        if self.selected is None:
            return
        self.accept()
