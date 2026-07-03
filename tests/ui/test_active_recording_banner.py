"""Tests for the ActiveRecordingBanner widget."""

from __future__ import annotations

import pytest

from teams_transcriber.ui.active_recording_banner import ActiveRecordingBanner


def test_banner_hidden_by_default(qapp) -> None:
    banner = ActiveRecordingBanner()
    assert banner.isHidden() is True
    assert banner.current_recording_id() is None


def test_banner_shows_on_show_recording(qapp) -> None:
    banner = ActiveRecordingBanner()
    banner.show()  # required so isVisible() can return True in offscreen
    banner.show_recording(42, "Test Meeting")
    assert banner.current_recording_id() == 42


def test_banner_hide_clears_state(qapp) -> None:
    banner = ActiveRecordingBanner()
    banner.show_recording(42, "Test Meeting")
    banner.hide_banner()
    assert banner.current_recording_id() is None


def test_banner_emits_clicked_with_recording_id(qapp) -> None:
    banner = ActiveRecordingBanner()
    received: list[int] = []
    banner.clicked.connect(received.append)
    banner.show_recording(99, "Click Me")
    banner._emit_clicked()
    assert received == [99]


def test_banner_set_processing_updates_title_prefix(qapp) -> None:
    banner = ActiveRecordingBanner()
    banner.show_recording(7, "My Meeting", status_label="Recording")
    banner.set_processing()
    # The title is now an ElidedLabel — displayed .text() truncates to fit
    # the (unshown, unlaid-out) widget's width, so assert on the full text.
    assert "Processing" in banner._title_label.full_text()
    assert "My Meeting" in banner._title_label.full_text()


def test_banner_set_processing_stops_timer_and_clears_elapsed(qapp) -> None:
    banner = ActiveRecordingBanner()
    banner.show_recording(5, "Brief Test", status_label="Recording")
    assert banner._timer.isActive() is True
    banner.set_processing()
    assert banner._timer.isActive() is False
    # The time label should not be showing a clock-style mm:ss anymore.
    assert ":" not in banner._elapsed_label.text()


def test_banner_elides_long_titles(qapp):
    from teams_transcriber.ui.active_recording_banner import ActiveRecordingBanner
    b = ActiveRecordingBanner()
    # 400px: wide enough that the fixed-width siblings (dot, elapsed, "Open
    # workspace" button, ~306px combined) leave the title some room, but not
    # enough to fit this title in full — it should elide.
    b.resize(400, 48)
    b.show_recording(1, "An enormously long meeting title that cannot fit in the banner at all")
    assert b._title_label.toolTip().startswith("Recording: An enormously")
    assert b._title_label.text().endswith("…")
    b.hide_banner()
