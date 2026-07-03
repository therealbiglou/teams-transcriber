"""Unit tests for the deferred-processing workspace tracker.

Constructing a full ``App`` is impractical (it builds tray/pipeline/window
and may run the first-run wizard), so these tests exercise the small,
pure-logic ``_WorkspaceTracker`` helper directly. No QApplication needed.
"""

from teams_transcriber.ui.app import _WorkspaceTracker


def test_tracker_marks_open_and_closed():
    t = _WorkspaceTracker()
    assert t.is_open(123) is False
    t.mark_open(123)
    assert t.is_open(123) is True
    t.mark_closed(123)
    assert t.is_open(123) is False


def test_tracker_closed_is_idempotent():
    t = _WorkspaceTracker()
    t.mark_closed(999)  # no error
    assert t.is_open(999) is False
