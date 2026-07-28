"""One row in the meeting history: title, when, status chip, actions.

The row is inert apart from its buttons and — when the meeting failed — its
chip, which opens a detail dialog carrying the full error.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from teams_transcriber.storage.models import Recording
from teams_transcriber.ui.confirm_dialog import ConfirmDialog
from teams_transcriber.ui.labels import ElidedLabel, make_selectable
from teams_transcriber.ui.meeting_status import RowAction, RowState

_MAX_ERROR_CHARS = 2000


def format_when(started_at: str, duration_ms: int | None) -> str:
    """'Jul 26, 3:31 PM · 38 min' — duration omitted when unknown."""
    try:
        dt = datetime.fromisoformat(started_at).astimezone()
        stamp = dt.strftime("%b %d, %I:%M %p").replace(" 0", " ")
    except ValueError:
        stamp = started_at
    if not duration_ms:
        return stamp
    return f"{stamp} · {round(duration_ms / 60000)} min"


class _ClickableChipLabel(ElidedLabel):
    """An ElidedLabel that emits ``clicked`` on mouse press."""

    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # Qt signature, no param annotation
        self.clicked.emit()
        super().mousePressEvent(event)


class MeetingRow(QFrame):
    action_requested = Signal(int, str)   # recording_id, RowAction value
    delete_requested = Signal(int)        # recording_id

    def __init__(
        self, recording: Recording, state: RowState, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        assert recording.id is not None
        self._recording_id = recording.id
        self._state = state

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.title_label = ElidedLabel(recording.display_title or "Untitled meeting")
        text_col.addWidget(self.title_label)

        self.when_label = make_selectable(
            ElidedLabel(format_when(recording.started_at, recording.duration_ms))
        )
        self.when_label.setProperty("role", "muted")
        text_col.addWidget(self.when_label)
        outer.addLayout(text_col, 1)

        self.chip_label = _ClickableChipLabel(state.chip)
        self.chip_label.setProperty("role", "chip")
        if state.error_message:
            self.chip_label.setCursor(Qt.CursorShape.PointingHandCursor)
            self.chip_label.setToolTip("Click for details")
            self.chip_label.clicked.connect(self.show_error_detail)
        outer.addWidget(self.chip_label)

        self.action_button: QPushButton | None = None
        if state.action is not RowAction.NONE and state.action_label:
            self.action_button = QPushButton(state.action_label)
            self.action_button.setProperty("role", "primary")
            self.action_button.clicked.connect(
                lambda: self.action_requested.emit(self._recording_id, state.action.value)
            )
            outer.addWidget(self.action_button)

        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("role", "ghost")
        self.delete_button.clicked.connect(
            lambda: self.delete_requested.emit(self._recording_id)
        )
        outer.addWidget(self.delete_button)

    def show_error_detail(self) -> None:
        """Open the failure dialog. No-op when the row isn't a failure."""
        if not self._state.error_message:
            return
        body = self._state.error_message[:_MAX_ERROR_CHARS]
        if len(self._state.error_message) > _MAX_ERROR_CHARS:
            body += "\n\n(truncated)"
        ConfirmDialog.info(
            self, title=self._state.chip, body=body, ok_label="Close", selectable=True,
        )
