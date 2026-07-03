"""Themed frameless modal: pick destination + format + assignee per SyncItem.

Replaces WrikeFolderPicker as the entry point for both auto-sync and manual
"Send to Wrike". WrikeFolderPicker still exists and is opened inline from each
row's destination button (DRY)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from teams_transcriber.integrations.wrike_assignees import Contact
from teams_transcriber.integrations.wrike_items import SyncItem
from teams_transcriber.integrations.wrike_sync import PlanRow
from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.title_bar import TitleBar


_FORMAT_OPTIONS: dict[str, list[str]] = {
    "summary":      ["Comment", "Task"],
    "decisions":    ["Comment", "Task"],
    "my_todo":      ["Task"],
    "action_other": ["Task"],
    "follow_up":    ["Task", "Comment"],
}
_DEFAULT_FORMAT: dict[str, str] = {
    "summary": "Comment", "decisions": "Comment",
    "my_todo": "Task", "action_other": "Task", "follow_up": "Task",
}
_KIND_LABEL: dict[str, str] = {
    "summary": "Summary", "decisions": "Decisions",
    "my_todo": "My todo", "action_other": "Action item", "follow_up": "Follow-up",
}


def _preview(text: str, max_chars: int = 80) -> str:
    one = " ".join(text.split())
    return one if len(one) <= max_chars else one[: max_chars - 1] + "…"


def _label_to_format(label: str) -> str:
    return "task" if label == "Task" else "comment"


class _WidthPinScrollArea(QScrollArea):
    """Scroll area that caps its inner container to the viewport width on
    resize, so a long unbroken token can't push the rows past the column edge.
    This is the project's documented third overflow guard (alongside
    ScrollBarAsNeeded + per-label Ignored/Preferred size policy)."""

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        inner = self.widget()
        viewport = self.viewport()
        if inner is not None and viewport is not None:
            inner.setMaximumWidth(viewport.width())


class WrikeSyncPlanner(FramelessWindowMixin, QDialog):
    def __init__(
        self,
        *,
        items: list[SyncItem],
        folders: list[dict[str, Any]],
        recent_folder_ids: list[str],
        contacts: list[Contact],
        assignee_suggestions: dict[int, str | None],   # items-list-index → contact_id
        already_synced_keys: Iterable[tuple[str, int]],  # set of (kind, index)
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Send to Wrike")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(720, 520)

        self._items = items
        self._folders = folders
        self._folder_by_id = {f["id"]: f for f in folders}
        self._contacts = contacts
        self._already_synced = set(already_synced_keys)
        self._rows: list[dict[str, Any]] = []
        self._default_folder_id: str | None = (
            recent_folder_ids[0] if recent_folder_ids
            else (folders[0]["id"] if folders else None)
        )

        frame = QFrame(); frame.setObjectName("OuterFrame")
        shell = QVBoxLayout(self)
        shell.addWidget(frame)
        inner = QVBoxLayout(frame); inner.setContentsMargins(0, 0, 0, 0); inner.setSpacing(0)

        self._title_bar = TitleBar(title="Send to Wrike", controls=("close",))
        self._title_bar.close_requested.connect(self.reject)
        inner.addWidget(self._title_bar)

        body = QWidget()
        v = QVBoxLayout(body); v.setContentsMargins(16, 12, 16, 12); v.setSpacing(10)

        scroll = _WidthPinScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        rows_host = QWidget()
        rows_layout = QVBoxLayout(rows_host); rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(8)
        scroll.setWidget(rows_host)
        v.addWidget(scroll, 1)

        for i, item in enumerate(items):
            suggested = assignee_suggestions.get(i)
            row_widget, row_state = self._build_row(i, item, suggested)
            rows_layout.addWidget(row_widget)
            self._rows.append(row_state)

        rows_layout.addStretch(1)

        footer = QHBoxLayout(); footer.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.setProperty("role", "secondary")
        cancel.clicked.connect(self.reject); footer.addWidget(cancel)
        self._send_btn = QPushButton("")
        self._send_btn.setObjectName("send-btn")
        self._send_btn.setProperty("role", "primary"); self._send_btn.setDefault(True)
        self._send_btn.clicked.connect(self._on_accept)
        footer.addWidget(self._send_btn)
        v.addLayout(footer)

        inner.addWidget(body, 1)
        self._init_frameless(frame, resizable=True, title_bar=self._title_bar,
                             shell_layout=shell)
        self._refresh_footer()

    def _build_row(self, item_idx, item, suggested_assignee_id):
        row = QFrame(); row.setProperty("card", True)
        rl = QVBoxLayout(row); rl.setContentsMargins(12, 8, 12, 8); rl.setSpacing(6)
        top = QHBoxLayout(); top.setSpacing(8)

        cb = QCheckBox(); cb.setObjectName("row-include"); cb.setChecked(True)
        top.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)

        kind_chip = QLabel(_KIND_LABEL[item.kind]); kind_chip.setProperty("role", "chip")
        kind_chip.setMaximumWidth(120)
        top.addWidget(kind_chip)

        preview = QLabel(_preview(item.text)); preview.setWordWrap(True); preview.setMinimumWidth(0)
        preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
        top.addWidget(preview, 1)

        synced_badge = QLabel("✓ synced"); synced_badge.setProperty("role", "chip")
        top.addWidget(synced_badge)

        format_cb = QComboBox(); format_cb.setObjectName("row-format")
        for opt in _FORMAT_OPTIONS[item.kind]:
            format_cb.addItem(opt)
        format_cb.setCurrentText(_DEFAULT_FORMAT[item.kind])
        top.addWidget(format_cb)

        dest_btn = QPushButton(); dest_btn.setObjectName("row-dest")
        dest_btn.setProperty("role", "secondary")
        dest_btn.setText(self._folder_label(self._default_folder_id))
        dest_btn.clicked.connect(lambda _=False, b=dest_btn: self._pick_folder(b))
        top.addWidget(dest_btn)

        rl.addLayout(top)

        assignee_cb = None
        if item.kind == "action_other":
            assignee_row = QHBoxLayout(); assignee_row.setContentsMargins(28, 0, 0, 0)
            assignee_label = QLabel("Assignee:"); assignee_label.setProperty("role", "muted")
            assignee_row.addWidget(assignee_label)
            assignee_cb = QComboBox(); assignee_cb.setObjectName("row-assignee")
            assignee_cb.setEditable(True)
            assignee_cb.addItem("Unassigned", userData=None)
            for c in self._contacts:
                assignee_cb.addItem(c.full_name, userData=c.id)
            if suggested_assignee_id is not None:
                for j in range(assignee_cb.count()):
                    if assignee_cb.itemData(j) == suggested_assignee_id:
                        assignee_cb.setCurrentIndex(j); break
            assignee_row.addWidget(assignee_cb, 1)
            rl.addLayout(assignee_row)

        state = {
            "item": item, "item_idx": item_idx, "checkbox": cb,
            "format_combo": format_cb, "dest_button": dest_btn,
            "dest_folder_id": self._default_folder_id,
            "assignee_combo": assignee_cb, "synced_badge": synced_badge,
            "locked": (item.kind, item.index) in self._already_synced,
        }
        if state["locked"]:
            cb.setEnabled(False); format_cb.setEnabled(False); dest_btn.setEnabled(False)
            if assignee_cb is not None:
                assignee_cb.setEnabled(False)
        else:
            synced_badge.setVisible(False)

        cb.toggled.connect(self._refresh_footer)
        format_cb.currentTextChanged.connect(self._refresh_footer)
        return row, state

    def _folder_label(self, folder_id):
        if folder_id is None:
            return "Pick a folder…"
        f = self._folder_by_id.get(folder_id)
        return f["title"] if f else "Pick a folder…"

    def _pick_folder(self, dest_btn):
        from teams_transcriber.ui.wrike_folder_picker import WrikeFolderPicker
        recent = [self._default_folder_id] if self._default_folder_id else []
        dlg = WrikeFolderPicker(folders=self._folders, recent_folder_ids=recent, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.selected_folder_id:
            return
        fid = dlg.selected_folder_id
        dest_btn.setText(self._folder_label(fid))
        for state in self._rows:
            if state["dest_button"] is dest_btn:
                state["dest_folder_id"] = fid; break
        self._refresh_footer()

    def _refresh_footer(self):
        count = sum(1 for s in self._rows if not s["locked"] and s["checkbox"].isChecked())
        self._send_btn.setText(f"Send {count} →")
        can_send = count > 0 and all(
            s["dest_folder_id"] is not None
            for s in self._rows if not s["locked"] and s["checkbox"].isChecked()
        )
        self._send_btn.setEnabled(can_send)

    def _on_accept(self):
        self.accept()

    def build_plan(self) -> list[PlanRow]:
        out: list[PlanRow] = []
        for s in self._rows:
            if s["locked"] or not s["checkbox"].isChecked():
                continue
            # Send is disabled while any checked row lacks a folder, so this is
            # normally unreachable — but guard for real (not via assert, which
            # -O strips) in case build_plan is ever called from another path.
            if s["dest_folder_id"] is None:
                continue
            assignee = None
            if s["assignee_combo"] is not None:
                # itemData(currentIndex()): the selected contact id, or None for
                # "Unassigned". A typed-but-unmatched entry has index -1 →
                # itemData(-1) is None, so it falls through to Unassigned.
                idx = s["assignee_combo"].currentIndex()
                assignee = s["assignee_combo"].itemData(idx)
            out.append(PlanRow(
                item=s["item"], folder_id=s["dest_folder_id"],
                format=_label_to_format(s["format_combo"].currentText()),
                assignee_id=assignee,
            ))
        return out
