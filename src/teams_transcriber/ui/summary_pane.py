"""Right-side detail pane: full summary + interactive my-todos."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.storage import (
    Database,
    RecordingRepo,
    Summary,
    SummaryRepo,
    TodoStateRepo,
    TranscriptRepo,
)
from teams_transcriber.ui.live_transcript_view import LiveTranscriptView


class SummaryPane(QScrollArea):
    """Right-side scroll panel showing one recording's summary."""

    export_requested = Signal(int)      # recording_id
    delete_requested = Signal(int)      # recording_id (caller confirms + deletes)
    notes_requested = Signal(int)       # recording_id — open notes editor
    retry_requested = Signal(int)       # recording_id — re-run from the failed step

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = db
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        # AsNeeded so a single un-wrappable long token doesn't crash visibility —
        # user can scroll horizontally if it happens. resizeEvent below also caps
        # the container width so well-behaved content fits cleanly.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)
        self.setWidget(self._container)
        self._current_recording_id: int | None = None

    def resizeEvent(self, e: QResizeEvent) -> None:
        # Cap the inner container's max width to the viewport so children must
        # shrink/wrap rather than push past the column edge.
        super().resizeEvent(e)
        viewport = self.viewport()
        if viewport is not None:
            self._container.setMaximumWidth(viewport.width())

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
            from teams_transcriber.storage import RecordingStatus
            if rec.status in (
                RecordingStatus.RECORDING_FAILED,
                RecordingStatus.TRANSCRIPTION_FAILED,
                RecordingStatus.SUMMARY_FAILED,
            ):
                widgets = [
                    _failure_status_label(rec.status),
                    _failure_message_label(rec.error_message or "(no detail)"),
                ]
                if rec.status in (
                    RecordingStatus.TRANSCRIPTION_FAILED,
                    RecordingStatus.SUMMARY_FAILED,
                ):
                    retry_btn = QPushButton("Retry")
                    retry_btn.setProperty("role", "primary")
                    retry_btn.clicked.connect(
                        lambda _checked=False, rid=recording_id:
                        self.retry_requested.emit(rid),
                    )
                    widgets.append(retry_btn)
                self._layout.addWidget(_section_card("Failed", widgets))
            else:
                self._layout.addWidget(QLabel("No summary yet for this recording."))
            return

        from PySide6.QtWidgets import QSizePolicy
        title = QLabel(rec.display_title or summary.title or "Untitled meeting")
        title.setProperty("role", "title")
        title.setWordWrap(True)
        title.setMinimumWidth(0)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._layout.addWidget(title)

        meta = QLabel(
            f"{_fmt_meta_time(rec.started_at)} · {(rec.duration_ms or 0) / 60000:.0f} min · {summary.model_used}"
        )
        meta.setProperty("role", "muted")
        meta.setWordWrap(True)
        meta.setMinimumWidth(0)
        meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._layout.addWidget(meta)

        if summary.summary:
            self._layout.addWidget(_section_card("Summary", [QLabel(summary.summary)]))

        if rec.manual_notes:
            notes_label = QLabel()
            notes_label.setTextFormat(Qt.TextFormat.RichText)
            notes_label.setText(rec.manual_notes)
            self._layout.addWidget(_section_card("My notes", [notes_label]))

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

        # Inline transcript (collapsed by default).
        segments = TranscriptRepo(self._db).list_for_recording(recording_id)
        if segments:
            self._layout.addWidget(self._build_transcript_card(segments))

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        notes_btn = QPushButton("Edit notes" if rec.manual_notes else "Add notes")
        notes_btn.setProperty("role", "secondary")
        notes_btn.clicked.connect(lambda: self.notes_requested.emit(recording_id))
        buttons.addWidget(notes_btn)

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

        delete_btn = QPushButton("Delete")
        delete_btn.setProperty("role", "danger")
        delete_btn.clicked.connect(lambda: self.delete_requested.emit(recording_id))
        buttons.addWidget(delete_btn)

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

    def _build_transcript_card(self, segments: list) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        header = QLabel("Transcript")
        header.setStyleSheet("font-size: 14px; font-weight: 600;")
        header_row.addWidget(header)
        header_row.addStretch(1)
        toggle = QPushButton("Show")
        toggle.setProperty("role", "ghost")
        toggle.setCheckable(True)
        header_row.addWidget(toggle)
        layout.addLayout(header_row)

        view = LiveTranscriptView()
        view.load_segments(segments)
        view.setVisible(False)
        view.setMinimumHeight(280)
        layout.addWidget(view)

        def _toggle(checked: bool) -> None:
            view.setVisible(checked)
            toggle.setText("Hide" if checked else "Show")
        toggle.toggled.connect(_toggle)
        return card

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
            w.setMinimumWidth(0)
            # QLabel with word wrap reports minSizeHint = longest-word-width by
            # default, which can push the column wide for long tokens. Telling
            # the size policy to ignore the natural width lets it shrink.
            from PySide6.QtWidgets import QSizePolicy
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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
        chip.setWordWrap(True)
        chip.setMaximumWidth(280)
        flow.addWidget(chip)
    layout.addWidget(chips_wrapper)
    return card


def _fmt_meta_time(iso: str) -> str:
    """Format a stored UTC ISO timestamp as local time."""
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso).astimezone()
        return dt.strftime("%b %d, %Y, %I:%M %p").lstrip("0").replace(" 0", " ")
    except ValueError:
        return iso


def _failure_status_label(status) -> "QLabel":
    from teams_transcriber.storage import RecordingStatus
    label_map = {
        RecordingStatus.RECORDING_FAILED:    "Recording failed",
        RecordingStatus.TRANSCRIPTION_FAILED: "Transcription failed",
        RecordingStatus.SUMMARY_FAILED:      "Summarization failed",
    }
    text = label_map.get(status, "Failed")
    label = QLabel(text)
    label.setStyleSheet("color: #DC2626; font-size: 14px; font-weight: 600;")
    return label


def _failure_message_label(msg: str) -> "QLabel":
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSizePolicy
    label = QLabel(msg)
    label.setWordWrap(True)
    label.setStyleSheet("color: #374151; font-size: 13px;")
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard,
    )
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label
