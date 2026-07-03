"""Chat-with-Claude card for the SummaryPane.

Self-contained: renders the persisted conversation history, exposes a
single-line autoresizing input + Send button, and emits send_requested
when the user submits a message. The App owns the network call.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.storage.chat import ChatMessage


class _ChatInput(QTextEdit):
    """QTextEdit where Enter submits and Shift+Enter inserts a newline."""

    submit_requested = Signal()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            e.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submit_requested.emit()
            e.accept()
            return
        super().keyPressEvent(e)


class ChatCard(QFrame):
    """A meeting's chat-with-Claude card. Sized to embed in SummaryPane."""

    send_requested = Signal(int, str)   # recording_id, user_text

    def __init__(
        self,
        recording_id: int,
        history: Iterable[ChatMessage],
        *,
        enabled: bool = True,
        disabled_hint: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._recording_id = recording_id
        self._enabled = enabled

        self.setProperty("card", True)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(12); shadow.setColor(QColor(0, 0, 0, 14))
        shadow.setOffset(0, 1); self.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16); outer.setSpacing(8)

        header = QLabel("Chat about this meeting")
        header.setStyleSheet("font-size: 14px; font-weight: 600;")
        outer.addWidget(header)

        # Scrollable message list.
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(180); scroll.setMaximumHeight(360)
        self._message_container = QWidget()
        self._msg_layout = QVBoxLayout(self._message_container)
        self._msg_layout.setContentsMargins(0, 0, 0, 0); self._msg_layout.setSpacing(6)
        scroll.setWidget(self._message_container)
        outer.addWidget(scroll, 1)
        self._scroll = scroll

        # Empty-state placeholder.
        self._placeholder = QLabel("Ask Claude about this meeting…")
        self._placeholder.setProperty("role", "muted")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._msg_layout.addWidget(self._placeholder)

        # Render existing history.
        for msg in history:
            self._add_bubble(msg.role, msg.content)

        # Disabled hint (only shown when not enabled).
        self._disabled_label = QLabel(disabled_hint or "")
        self._disabled_label.setWordWrap(True)
        self._disabled_label.setStyleSheet("color: #B45309; font-size: 12px;")
        self._disabled_label.setVisible(not enabled and bool(disabled_hint))
        outer.addWidget(self._disabled_label)

        # Input row.
        row = QHBoxLayout(); row.setSpacing(8)
        self._input = _ChatInput()
        self._input.setPlaceholderText(
            "Ask a question… (Enter to send, Shift+Enter for newline)"
        )
        self._input.setMinimumHeight(36)
        self._input.setMaximumHeight(120)
        self._input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )
        self._input.setEnabled(enabled)
        self._input.submit_requested.connect(self._on_send_clicked)
        row.addWidget(self._input, 1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setProperty("role", "primary")
        self._send_btn.setFixedHeight(36)
        self._send_btn.setEnabled(enabled)
        self._send_btn.clicked.connect(self._on_send_clicked)
        row.addWidget(self._send_btn)
        outer.addLayout(row)

    # ---- public API ---------------------------------------------------

    def append_user_message(self, text: str) -> None:
        self._add_bubble("user", text)

    def append_assistant_message(self, text: str) -> None:
        self._add_bubble("assistant", text)

    def append_error_message(self, text: str) -> None:
        self._add_bubble("error", text)

    def set_pending(self, pending: bool) -> None:
        self._input.setEnabled(self._enabled and not pending)
        self._send_btn.setEnabled(self._enabled and not pending)
        self._send_btn.setText("Sending…" if pending else "Send")

    # ---- internals ----------------------------------------------------

    def _on_send_clicked(self) -> None:
        if not self._enabled:
            return
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._input.clear()
        self.send_requested.emit(self._recording_id, text)

    def _add_bubble(self, role: str, content: str) -> None:
        from PySide6.QtCore import QTimer

        from teams_transcriber.ui.labels import make_selectable, make_wrapping

        if self._placeholder is not None:
            self._placeholder.setVisible(False)
        bubble = QLabel(content)
        bubble.setTextFormat(Qt.TextFormat.PlainText)
        make_wrapping(bubble)
        make_selectable(bubble)
        if role == "user":
            bubble.setStyleSheet(
                "background: #10B981; color: white; border-radius: 10px; padding: 8px;"
            )
        elif role == "error":
            bubble.setStyleSheet(
                "background: #FEE2E2; color: #991B1B; border-radius: 10px; "
                "padding: 8px; border: 1px solid #FCA5A5;"
            )
        else:
            bubble.setStyleSheet(
                "background: #FFFFFF; color: #111827; border-radius: 10px; "
                "padding: 8px; border: 1px solid #E5E7EB;"
            )
        self._msg_layout.addWidget(bubble)

        # Scroll after the layout pass, not before it — bar.maximum() is stale
        # until the new bubble has a height.
        def scroll_to_bottom() -> None:
            bar = self._scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        # Pass self as the QObject context: Qt drops the callback if the card
        # is destroyed before the timer fires, so we never touch a deleted
        # C++ object.
        QTimer.singleShot(0, self, scroll_to_bottom)
