"""Right-side detail pane: full summary + interactive my-todos."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.storage import (
    Database,
    RecordingRepo,
    Summary,
    SummaryRepo,
    TodoStateRepo,
)
from teams_transcriber.ui.icons import IconName, get_icon
from teams_transcriber.ui.theme import COLORS


class SummaryPane(QScrollArea):
    """Right-side scroll panel showing one recording's summary."""

    transcript_requested = Signal(int)  # recording_id
    export_requested = Signal(int)      # recording_id
    delete_requested = Signal(int)      # recording_id (caller confirms + deletes)

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = db
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)
        self.setWidget(self._container)
        self._current_recording_id: int | None = None

    def clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._current_recording_id = None

    def show_recording(self, recording_id: int) -> None:
        self.clear()
        self._current_recording_id = recording_id
        rec_repo = RecordingRepo(self._db)
        sum_repo = SummaryRepo(self._db)
        rec = rec_repo.get(recording_id)
        summary = sum_repo.get(recording_id)
        if rec is None:
            self._layout.addWidget(QLabel("Recording not found."))
            return
        if summary is None:
            self._layout.addWidget(QLabel("No summary yet for this recording."))
            return

        # Header row: title (wraps) + delete icon button pinned top-right.
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        title = QLabel(rec.display_title or summary.title or "Untitled meeting")
        title.setProperty("role", "title")
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header.addWidget(title, 1)

        delete_icon_btn = QPushButton()
        delete_icon_btn.setIcon(get_icon(IconName.CLOSE, color=COLORS["red"]))
        delete_icon_btn.setToolTip("Delete this recording")
        delete_icon_btn.setProperty("role", "ghost")
        delete_icon_btn.setFixedSize(32, 32)
        delete_icon_btn.clicked.connect(lambda: self.delete_requested.emit(recording_id))
        header.addWidget(delete_icon_btn, 0, Qt.AlignmentFlag.AlignTop)

        header_wrapper = QWidget()
        header_wrapper.setLayout(header)
        self._layout.addWidget(header_wrapper)

        meta = QLabel(
            f"{rec.started_at} · {(rec.duration_ms or 0) / 60000:.0f} min · {summary.model_used}"
        )
        meta.setProperty("role", "muted")
        meta.setWordWrap(True)
        self._layout.addWidget(meta)

        if summary.summary:
            self._layout.addWidget(_section_card("Summary", [QLabel(summary.summary)]))

        if summary.my_todos:
            self._layout.addWidget(self._build_todos_card(summary))

        if summary.action_items_others:
            widgets: list[QWidget] = [
                QLabel(f"• {a.who} — {a.task}" + (f" (due {a.due})" if a.due else ""))
                for a in summary.action_items_others
            ]
            self._layout.addWidget(_section_card("Action items for others", widgets))

        if summary.key_decisions:
            widgets = [QLabel(f"• {d}") for d in summary.key_decisions]
            self._layout.addWidget(_section_card("Key decisions", widgets))

        if summary.follow_ups:
            widgets = [QLabel(f"• {f}") for f in summary.follow_ups]
            self._layout.addWidget(_section_card("Follow-ups", widgets))

        if summary.topics:
            self._layout.addWidget(_topics_row(summary.topics))

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        view_btn = QPushButton("Transcript")
        view_btn.setProperty("role", "secondary")
        view_btn.clicked.connect(lambda: self.transcript_requested.emit(recording_id))
        buttons.addWidget(view_btn)

        copy_btn = QPushButton("Copy")
        copy_btn.setToolTip("Copy summary as markdown")
        copy_btn.setProperty("role", "secondary")
        copy_btn.clicked.connect(lambda: self._copy_markdown(summary, rec))
        buttons.addWidget(copy_btn)

        export_btn = QPushButton("Export")
        export_btn.setProperty("role", "primary")
        export_btn.clicked.connect(lambda: self.export_requested.emit(recording_id))
        buttons.addWidget(export_btn)

        buttons.addStretch(1)

        wrapper = QWidget()
        wrapper.setLayout(buttons)
        self._layout.addWidget(wrapper)

        self._layout.addStretch(1)

    def _build_todos_card(self, summary: Summary) -> QFrame:
        todo_repo = TodoStateRepo(self._db)
        existing = {s.todo_index: s for s in todo_repo.list_for_recording(summary.recording_id)}

        rows: list[QWidget] = []
        for i, td in enumerate(summary.my_todos):
            cb = QCheckBox(td.task + (f"  (due {td.due})" if td.due else ""))
            state = existing.get(i)
            if state is not None and state.done:
                cb.setChecked(True)
            cb.toggled.connect(
                lambda checked, idx=i, task=td.task:
                todo_repo.upsert(summary.recording_id, idx, task, checked)
            )
            rows.append(cb)
        return _section_card("My todos", rows)

    def _copy_markdown(self, summary: Summary, recording: Any) -> None:
        lines = [f"# {summary.title or recording.display_title or 'Meeting'}", ""]
        if summary.summary:
            lines += [summary.summary, ""]
        if summary.my_todos:
            lines.append("## My todos")
            for t in summary.my_todos:
                lines.append(f"- [ ] {t.task}" + (f" (due {t.due})" if t.due else ""))
            lines.append("")
        if summary.action_items_others:
            lines.append("## Action items for others")
            for a in summary.action_items_others:
                lines.append(f"- {a.who}: {a.task}" + (f" (due {a.due})" if a.due else ""))
            lines.append("")
        if summary.key_decisions:
            lines.append("## Key decisions")
            lines += [f"- {d}" for d in summary.key_decisions]
            lines.append("")
        if summary.follow_ups:
            lines.append("## Follow-ups")
            lines += [f"- {f}" for f in summary.follow_ups]
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("\n".join(lines))


def _section_card(title: str, body_widgets: list[QWidget]) -> QFrame:
    card = QFrame()
    card.setProperty("card", True)
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(12)
    shadow.setColor(QColor(0, 0, 0, 14))
    shadow.setOffset(0, 1)
    card.setGraphicsEffect(shadow)

    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(8)
    header = QLabel(title)
    header.setStyleSheet("font-size: 14px; font-weight: 600;")
    layout.addWidget(header)
    for w in body_widgets:
        if isinstance(w, QLabel):
            w.setWordWrap(True)
        layout.addWidget(w)
    return card


def _topics_row(topics: list[str]) -> QFrame:
    from teams_transcriber.ui.flow_layout import FlowLayout

    card = QFrame()
    card.setProperty("card", True)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(8)
    header = QLabel("Topics")
    header.setStyleSheet("font-size: 14px; font-weight: 600;")
    layout.addWidget(header)

    chips_wrapper = QWidget()
    flow = FlowLayout(chips_wrapper, margin=0, spacing=6)
    for t in topics:
        chip = QLabel(t)
        chip.setProperty("role", "chip")
        flow.addWidget(chip)
    layout.addWidget(chips_wrapper)
    return card
