"""Right-side detail pane: full summary + interactive my-todos."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt as _Qt
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
from teams_transcriber.ui.labels import make_selectable as _make_selectable
from teams_transcriber.ui.labels import make_todo_row as _make_todo_row


class SummaryPane(QScrollArea):
    """Right-side scroll panel showing one recording's summary."""

    export_requested = Signal(int)       # recording_id
    delete_requested = Signal(int)       # recording_id (caller confirms + deletes)
    notes_requested = Signal(int)        # recording_id — open notes editor
    retry_requested = Signal(int)        # recording_id — re-run from the failed step
    transcript_requested = Signal(int)   # recording_id — open transcript window
    todo_state_changed = Signal(int)     # recording_id — a checkbox toggled
    wrike_sync_requested = Signal(int)   # recording_id — manually send todos to Wrike
    chat_send_requested = Signal(int, str)   # recording_id, user_text

    def __init__(
        self,
        db: Database,
        *,
        wrike_available: Callable[[], bool] | None = None,
        anthropic_key_getter: Callable[[], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        # Returns True when Wrike is configured (token present + enabled), so
        # the "Send to Wrike" button only appears when it can actually do
        # something. None = never show (the integration's UI is hidden).
        self._wrike_available = wrike_available
        self._anthropic_key_getter = anthropic_key_getter
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
            self._layout.addWidget(_make_selectable(QLabel("Recording not found.")))
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
                delete_btn = QPushButton("Delete")
                delete_btn.setProperty("role", "danger")
                delete_btn.clicked.connect(
                    lambda _checked=False, rid=recording_id:
                    self.delete_requested.emit(rid),
                )
                widgets.append(delete_btn)
                self._layout.addWidget(_section_card("Failed", widgets))
            else:
                self._layout.addWidget(_make_selectable(QLabel("No summary yet for this recording.")))
            return

        from PySide6.QtWidgets import QSizePolicy
        title = _make_selectable(QLabel(rec.display_title or summary.title or "Untitled meeting"))
        title.setProperty("role", "title")
        title.setWordWrap(True)
        title.setMinimumWidth(0)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._layout.addWidget(title)

        meta = _make_selectable(QLabel(
            f"{_fmt_meta_time(rec.started_at)} · {(rec.duration_ms or 0) / 60000:.0f} min · {summary.model_used}"
        ))
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
            _make_selectable(notes_label)
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

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        transcript_btn = QPushButton("View transcript")
        transcript_btn.setProperty("role", "secondary")
        transcript_btn.clicked.connect(
            lambda: self.transcript_requested.emit(recording_id),
        )
        buttons.addWidget(transcript_btn)

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

        # "Send to Wrike" — only shown when the integration is configured.
        # Sync is idempotent, so re-clicking on an already-synced meeting is
        # safe (adds any new todos, skips ones already mapped).
        if self._wrike_available is not None and self._wrike_available():
            wrike_btn = QPushButton("Send to Wrike")
            wrike_btn.setToolTip(
                "Create Wrike tasks for this meeting's todos and action items "
                "for others (idempotent — clicking again only sends new ones)."
            )
            wrike_btn.setProperty("role", "secondary")
            wrike_btn.clicked.connect(
                lambda: self.wrike_sync_requested.emit(recording_id),
            )
            buttons.addWidget(wrike_btn)

        buttons.addStretch(1)

        delete_btn = QPushButton("Delete")
        delete_btn.setProperty("role", "danger")
        delete_btn.clicked.connect(lambda: self.delete_requested.emit(recording_id))
        buttons.addWidget(delete_btn)

        wrapper = QWidget()
        wrapper.setLayout(buttons)
        self._layout.addWidget(wrapper)

        # Chat-with-Claude card — only meaningful when we have transcript
        # segments to give Claude as context.
        from teams_transcriber.storage import TranscriptRepo
        from teams_transcriber.storage.chat import ChatRepo
        from teams_transcriber.ui.chat_card import ChatCard
        segments = TranscriptRepo(self._db).list_for_recording(recording_id)
        if segments:
            history = ChatRepo(self._db).list_for_recording(recording_id)
            api_key = (self._anthropic_key_getter or (lambda: ""))()
            if api_key:
                self._chat_card = ChatCard(
                    recording_id, history, enabled=True,
                )
            else:
                self._chat_card = ChatCard(
                    recording_id, history, enabled=False,
                    disabled_hint=(
                        "Set your Anthropic API key in Settings → AI to chat."
                    ),
                )
            self._chat_card.send_requested.connect(self.chat_send_requested.emit)
            self._layout.addWidget(self._chat_card)
        else:
            self._chat_card = None

        self._layout.addStretch(1)

    def _build_todos_card(self, summary: Summary) -> QFrame:
        todo_repo = TodoStateRepo(self._db)
        existing = {s.todo_index: s for s in todo_repo.list_for_recording(summary.recording_id)}

        rows: list[QWidget] = []
        for i, td in enumerate(summary.my_todos):
            text = td.task + (f"  (due {td.due})" if td.due else "")
            state = existing.get(i)
            checked = state is not None and state.done

            def _make_handler(idx: int, task: str) -> Callable[[bool], None]:
                def _on_toggle(is_checked: bool) -> None:
                    todo_repo.upsert(summary.recording_id, idx, task, is_checked)
                    self.todo_state_changed.emit(summary.recording_id)
                return _on_toggle

            rows.append(_make_todo_row(
                text, checked=checked, on_toggle=_make_handler(i, td.task),
            ))
        return _section_card("My todos", rows)

    def _copy_markdown(self, summary: Summary, recording: Any) -> None:
        from teams_transcriber import summary_export
        states = {
            s.todo_index: s.done
            for s in TodoStateRepo(self._db).list_for_recording(summary.recording_id)
        }
        md = summary_export.to_markdown(summary, recording, states)
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(md)


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
    header = _make_selectable(QLabel(title))
    header.setStyleSheet("font-size: 14px; font-weight: 600;")
    layout.addWidget(header)
    for w in body_widgets:
        if isinstance(w, QLabel):
            _make_selectable(w)
            w.setWordWrap(True)
            w.setMinimumWidth(0)
            # QLabel with word wrap reports minSizeHint = longest-word-width by
            # default, which can push the column wide for long tokens. Telling
            # the size policy to ignore the natural width lets it shrink.
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(w)
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
