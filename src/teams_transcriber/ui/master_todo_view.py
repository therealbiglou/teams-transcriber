"""Master to-do list: every to-do across meetings, grouped by meeting.

Interactive checkboxes write through TodoStateRepo (same store as the summary
pane), so the Phase-9 history completion chip and the summary stay in sync.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from teams_transcriber.storage import (
    Database, RecordingRepo, SummaryRepo, TodoStateRepo,
)


def _fmt_day(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%b %d, %Y")
    except ValueError:
        return iso


class MasterTodoView(QScrollArea):
    go_to_summary = Signal(int)   # recording_id
    todo_toggled = Signal(int)    # recording_id

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = db
        self._group_ids: list[int] = []
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)
        self.setWidget(self._container)

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        vp = self.viewport()
        if vp is not None:
            self._container.setMaximumWidth(vp.width())

    def group_count(self) -> int:
        return len(self._group_ids)

    def group_recording_ids(self) -> list[int]:
        return list(self._group_ids)

    def is_empty(self) -> bool:
        return not self._group_ids

    def reload(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._group_ids = []

        rec_repo = RecordingRepo(self._db)
        sum_repo = SummaryRepo(self._db)
        todo_repo = TodoStateRepo(self._db)
        for rec in rec_repo.list_recent(limit=500):
            if rec.id is None:
                continue
            s = sum_repo.get(rec.id)
            if s is None or not s.my_todos:
                continue
            states = {st.todo_index: st.done for st in todo_repo.list_for_recording(rec.id)}
            self._layout.addWidget(self._build_group(rec, s, states))
            self._group_ids.append(rec.id)

        if not self._group_ids:
            empty = QLabel("No to-dos yet.")
            empty.setProperty("role", "muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._layout.addWidget(empty)
        self._layout.addStretch(1)

    def _build_group(self, rec, summary, states: dict[int, bool]) -> QWidget:
        card = QWidget()
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel(rec.display_title or summary.title or "Meeting")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(title, 1)
        day = QLabel(_fmt_day(rec.started_at))
        day.setProperty("role", "muted")
        header.addWidget(day)
        go = QPushButton("Go to summary")
        go.setProperty("role", "secondary")
        go.clicked.connect(lambda _checked=False, rid=rec.id: self._emit_go_to_summary(rid))
        header.addWidget(go)
        v.addLayout(header)

        for i, td in enumerate(summary.my_todos):
            cb = QCheckBox(td.task + (f"  (due {td.due})" if td.due else ""))
            cb.setChecked(bool(states.get(i)))
            cb.toggled.connect(
                lambda checked, rid=rec.id, idx=i, task=td.task: self._toggle(rid, idx, task, checked)
            )
            v.addWidget(cb)
        return card

    def _emit_go_to_summary(self, recording_id: int) -> None:
        self.go_to_summary.emit(recording_id)

    def _toggle(self, recording_id: int, idx: int, task: str, checked: bool) -> None:
        TodoStateRepo(self._db).upsert(recording_id, idx, task, checked)
        self.todo_toggled.emit(recording_id)
