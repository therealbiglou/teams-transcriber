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
