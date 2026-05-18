"""Scrollable list of live transcript segments.

Each row: channel badge (ME = emerald pill, OTHERS = neutral pill),
mm:ss timestamp, segment text. Auto-scrolls to the bottom when new
segments arrive, but pauses auto-scroll when the user has scrolled up
to read earlier content.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QWidget,
)

from teams_transcriber.storage import Channel, TranscriptSegment


def _format_ts(ms: int) -> str:
    total = max(0, ms // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


def _channel_label(channel: Channel) -> tuple[str, str, str]:
    """Return (text, background_color, text_color) for the channel badge."""
    if channel == Channel.ME:
        return "ME", "#10B981", "#FFFFFF"      # emerald pill
    return "OTHERS", "#E5E7EB", "#111827"      # neutral pill


class _SegmentRow(QWidget):
    def __init__(self, segment: TranscriptSegment, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        badge_text, bg, fg = _channel_label(segment.channel)
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            f"background: {bg}; color: {fg}; "
            "border-radius: 8px; padding: 2px 8px; "
            "font-size: 11px; font-weight: 600;"
        )
        badge.setFixedHeight(20)
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        ts = QLabel(_format_ts(segment.start_ms))
        ts.setStyleSheet("color: #6B7280; font-size: 11px;")
        ts.setFixedWidth(48)
        layout.addWidget(ts, 0, Qt.AlignmentFlag.AlignTop)

        text = QLabel(segment.text)
        text.setWordWrap(True)
        text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
        text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(text, 1)


class LiveTranscriptView(QListWidget):
    """List of segments with smart auto-scroll."""

    RAW_TEXT_ROLE = Qt.ItemDataRole.UserRole + 1

    AUTO_SCROLL_BOTTOM_TOLERANCE_PX = 16

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            "QListWidget { background: #FFFFFF; border: 1px solid #E5E7EB; "
            "border-radius: 12px; }"
            "QListWidget::item { border-bottom: 1px solid #F3F4F6; }"
            "QListWidget::item:last { border-bottom: none; }"
        )

    def append_segment(self, segment: TranscriptSegment) -> None:
        was_at_bottom = self._is_scrolled_to_bottom()
        item = QListWidgetItem()
        item.setData(self.RAW_TEXT_ROLE, segment.text)
        row = _SegmentRow(segment)
        item.setSizeHint(row.sizeHint())
        self.addItem(item)
        self.setItemWidget(item, row)
        if was_at_bottom:
            self.scrollToBottom()

    def load_segments(self, segments: list[TranscriptSegment]) -> None:
        """Replace the current contents with a fixed batch (past-recording mode)."""
        self.clear()
        for s in segments:
            self.append_segment(s)
        self.scrollToTop()

    def _is_scrolled_to_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - self.AUTO_SCROLL_BOTTOM_TOLERANCE_PX
