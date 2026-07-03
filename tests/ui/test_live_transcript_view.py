"""Tests for the LiveTranscriptView widget.

The view is a single read-only QTextEdit holding the whole transcript as one
selectable document (smooth pixel scroll, drag-select/copy across rows).
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from teams_transcriber.storage import Channel, TranscriptSegment
from teams_transcriber.ui.live_transcript_view import LiveTranscriptView


def _seg(start_ms: int, channel: Channel, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        id=None, recording_id=1, start_ms=start_ms, end_ms=start_ms + 1000,
        channel=channel, text=text,
    )


def test_loads_segments_into_one_document(qapp) -> None:
    v = LiveTranscriptView()
    v.load_segments(
        [_seg(0, Channel.ME, "hello there"), _seg(2000, Channel.OTHERS, "general kenobi")],
    )
    plain = v.toPlainText()
    assert "hello there" in plain and "general kenobi" in plain
    assert "ME" in plain and "OTHERS" in plain
    assert "00:00" in plain and "00:02" in plain


def test_append_segment_adds_to_document(qapp) -> None:
    v = LiveTranscriptView()
    v.load_segments([_seg(0, Channel.ME, "first")])
    v.append_segment(_seg(1000, Channel.OTHERS, "second"))
    assert "first" in v.toPlainText() and "second" in v.toPlainText()


def test_append_multiple_segments_preserves_order(qapp) -> None:
    v = LiveTranscriptView()
    v.append_segment(_seg(0, Channel.ME, "first"))
    v.append_segment(_seg(1500, Channel.OTHERS, "second"))
    v.append_segment(_seg(3000, Channel.ME, "third"))
    plain = v.toPlainText()
    assert plain.index("first") < plain.index("second") < plain.index("third")


def test_load_segments_replaces_contents(qapp) -> None:
    v = LiveTranscriptView()
    v.append_segment(_seg(0, Channel.ME, "stale"))
    v.load_segments(
        [_seg(0, Channel.ME, "fresh-1"), _seg(1500, Channel.OTHERS, "fresh-2")],
    )
    plain = v.toPlainText()
    assert "stale" not in plain
    assert "fresh-1" in plain and "fresh-2" in plain


def test_is_read_only_and_selectable(qapp) -> None:
    v = LiveTranscriptView()
    assert v.isReadOnly()
    flags = v.textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
    assert flags & Qt.TextInteractionFlag.TextSelectableByKeyboard


def test_smooth_pixel_scroll_mode(qapp) -> None:
    v = LiveTranscriptView()
    assert v.verticalScrollBar().singleStep() <= 20


def test_html_is_escaped(qapp) -> None:
    v = LiveTranscriptView()
    v.load_segments([_seg(0, Channel.ME, "a < b & c > d")])
    assert "a < b & c > d" in v.toPlainText()


def test_autoscroll_pauses_when_user_scrolls_up(qapp) -> None:
    v = LiveTranscriptView()
    v.resize(200, 100)
    for i in range(60):
        v.append_segment(_seg(i * 1500, Channel.ME, f"line number {i} with some text"))
    bar = v.verticalScrollBar()
    bar.setValue(0)
    pos_before = bar.value()
    v.append_segment(_seg(99 * 1500, Channel.OTHERS, "new while scrolled up"))
    assert bar.value() == pos_before  # did not auto-scroll
