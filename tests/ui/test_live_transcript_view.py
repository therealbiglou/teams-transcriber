"""Tests for the LiveTranscriptView widget."""

from __future__ import annotations

from teams_transcriber.storage import Channel, TranscriptSegment
from teams_transcriber.ui.live_transcript_view import LiveTranscriptView


def _seg(start_ms: int, end_ms: int, channel: Channel, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        id=None, recording_id=1, start_ms=start_ms, end_ms=end_ms,
        channel=channel, text=text,
    )


def test_append_renders_segment(qapp) -> None:
    view = LiveTranscriptView()
    view.append_segment(_seg(0, 1500, Channel.ME, "hello"))
    assert view.count() == 1


def test_append_multiple_segments_preserves_order(qapp) -> None:
    view = LiveTranscriptView()
    view.append_segment(_seg(0, 1500, Channel.ME, "first"))
    view.append_segment(_seg(1500, 3000, Channel.OTHERS, "second"))
    view.append_segment(_seg(3000, 4500, Channel.ME, "third"))
    texts = [view.item(i).data(LiveTranscriptView.RAW_TEXT_ROLE) for i in range(view.count())]
    assert texts == ["first", "second", "third"]


def test_load_initial_segments_replaces_contents(qapp) -> None:
    view = LiveTranscriptView()
    view.append_segment(_seg(0, 1500, Channel.ME, "stale"))
    view.load_segments([
        _seg(0, 1500, Channel.ME, "fresh-1"),
        _seg(1500, 3000, Channel.OTHERS, "fresh-2"),
    ])
    assert view.count() == 2
    assert view.item(0).data(LiveTranscriptView.RAW_TEXT_ROLE) == "fresh-1"


def test_autoscroll_pauses_when_user_scrolls_up(qapp) -> None:
    view = LiveTranscriptView()
    view.resize(200, 100)
    for i in range(40):
        view.append_segment(_seg(i * 1500, (i + 1) * 1500, Channel.ME, f"line {i}"))
    bar = view.verticalScrollBar()
    bar.setValue(0)
    pos_before = bar.value()
    view.append_segment(_seg(99 * 1500, 100 * 1500, Channel.OTHERS, "new while scrolled up"))
    assert bar.value() == pos_before  # did not auto-scroll
