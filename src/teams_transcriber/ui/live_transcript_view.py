"""Read-only transcript document with smooth scroll + full selection.

One QTextEdit holds the whole transcript as a single selectable document, so
the user can drag-select/copy across many lines and scroll smoothly (per-pixel).
Each segment is one compact block: a colored channel tag, a mm:ss timestamp,
then the text. Live mode appends blocks via a cursor; smart auto-scroll keeps
the view pinned to the bottom only when the user is already at the bottom.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextEdit, QWidget

from teams_transcriber.storage import Channel, TranscriptSegment


def _format_ts(ms: int) -> str:
    total = max(0, ms // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


def _channel_color(channel: Channel) -> tuple[str, str]:
    """Return (label, text_color) for the inline channel tag."""
    if channel == Channel.ME:
        return "ME", "#10B981"      # emerald
    return "OTHERS", "#475569"      # slate


class LiveTranscriptView(QTextEdit):
    """Single-document transcript view (read-only, selectable, smooth scroll)."""

    AUTO_SCROLL_BOTTOM_TOLERANCE_PX = 16

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
        self.document().setDocumentMargin(8)
        self.setStyleSheet(
            "QTextEdit { background: #FFFFFF; border: 1px solid #E5E7EB; "
            "border-radius: 12px; padding: 0px; }"
        )

    def _segment_html(self, segment: TranscriptSegment) -> str:
        label, color = _channel_color(segment.channel)
        ts = _format_ts(segment.start_ms)
        text = html.escape(segment.text)
        return (
            f'<div style="margin:0 0 3px 0;">'
            f'<span style="color:{color}; font-weight:600; font-size:11px;">{label}</span> '
            f'<span style="color:#6B7280; font-size:11px;">{ts}</span> '
            f'<span style="color:#111827;">{text}</span>'
            f'</div>'
        )

    def append_segment(self, segment: TranscriptSegment) -> None:
        was_at_bottom = self._is_scrolled_to_bottom()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(self._segment_html(segment))
        if was_at_bottom:
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())

    def load_segments(self, segments: list[TranscriptSegment]) -> None:
        """Replace contents with a fixed batch (past-recording mode)."""
        self.clear()
        if not segments:
            return
        html_blocks = "".join(self._segment_html(s) for s in segments)
        self.setHtml(html_blocks)
        self.verticalScrollBar().setValue(0)

    def _is_scrolled_to_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - self.AUTO_SCROLL_BOTTOM_TOLERANCE_PX
