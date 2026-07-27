"""Tests for the WASAPI session probe (all pycaw calls mocked)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


def test_returns_pids_for_active_teams_sessions(monkeypatch):
    """When a Teams process holds an active capture session, return its PID."""
    sys.modules.pop("teams_transcriber.audio.wasapi_sessions", None)
    from teams_transcriber.audio import wasapi_sessions

    fake_sessions = [
        (1234, "ms-teams.exe", True),
        (5678, "ms-teams.exe", False),
        (9012, "spotify.exe", True),
        (3456, "Teams.exe", True),
    ]

    def fake_enum():
        return fake_sessions

    monkeypatch.setattr(wasapi_sessions, "_enumerate_active_capture_sessions", fake_enum)
    assert wasapi_sessions.teams_active_capture_pids() == {1234, 3456}


def test_returns_empty_set_when_enumeration_raises(monkeypatch):
    """If the COM call throws, return empty set + log a warning."""
    sys.modules.pop("teams_transcriber.audio.wasapi_sessions", None)
    from teams_transcriber.audio import wasapi_sessions

    def boom():
        raise OSError("COM not initialized")

    monkeypatch.setattr(wasapi_sessions, "_enumerate_active_capture_sessions", boom)
    assert wasapi_sessions.teams_active_capture_pids() == set()


def test_filters_only_active_state(monkeypatch):
    """Inactive (state != 1) sessions for Teams should be excluded."""
    sys.modules.pop("teams_transcriber.audio.wasapi_sessions", None)
    from teams_transcriber.audio import wasapi_sessions

    fake_sessions = [
        (100, "ms-teams.exe", False),  # inactive — skip
        (200, "ms-teams.exe", True),
    ]
    monkeypatch.setattr(
        wasapi_sessions, "_enumerate_active_capture_sessions",
        lambda: fake_sessions,
    )
    assert wasapi_sessions.teams_active_capture_pids() == {200}


def test_repeated_probe_failures_are_logged_once_not_every_poll(caplog):
    """A persistent COM failure must not write a line on every ~2s poll.

    Regression: 'Element not found' from GetSpeakers() (no default playback
    device) spammed a full traceback every poll, burying the whole log.
    """
    import logging

    from teams_transcriber.audio import wasapi_sessions as ws

    ws._probe_failure.update(signature=None, last_logged_at=0.0, suppressed=0)
    exc = OSError("Element not found.")
    with caplog.at_level(logging.DEBUG, logger=ws.logger.name):
        for _ in range(50):
            ws._note_probe_failure(exc)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, f"expected 1 warning for 50 identical failures, got {len(warnings)}"
    assert "no default audio" in warnings[0].getMessage()
    assert ws._probe_failure["suppressed"] == 49


def test_probe_recovery_is_logged_and_resets_state(caplog):
    import logging

    from teams_transcriber.audio import wasapi_sessions as ws

    ws._probe_failure.update(signature=None, last_logged_at=0.0, suppressed=0)
    ws._note_probe_failure(OSError("boom"))
    with caplog.at_level(logging.INFO, logger=ws.logger.name):
        ws._note_probe_recovered()
        ws._note_probe_recovered()  # already clear -> silent

    recovered = [r for r in caplog.records if "recovered" in r.getMessage()]
    assert len(recovered) == 1
    assert ws._probe_failure["signature"] is None
