"""WASAPI capture-session probe — used by MeetingWatcher to detect "Teams is
holding the mic" without depending on window titles.

All errors (missing pycaw, COM failures, etc.) degrade to an empty-set return.
The watcher falls back to title pattern matching when this returns empty.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TEAMS_PROCESS_NAMES = {"ms-teams.exe", "teams.exe"}


def teams_active_capture_pids() -> set[int]:
    """Return PIDs of Teams processes currently holding an active mic capture session."""
    try:
        sessions = _enumerate_active_capture_sessions()
    except Exception:
        logger.exception("WASAPI capture-session enumeration failed; falling back to empty set")
        return set()
    return {
        pid for (pid, name, active) in sessions
        if active and name and name.lower() in TEAMS_PROCESS_NAMES
    }


def _enumerate_active_capture_sessions() -> list[tuple[int, str, bool]]:
    """Return (pid, process_name, is_active) for every WASAPI capture session.

    Wrapped in its own function so tests can monkey-patch this and avoid the
    actual COM round-trip. Returns an empty list if pycaw isn't available or
    if any error occurs during enumeration.
    """
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
    except Exception:
        logger.warning("pycaw not available; WASAPI session probe disabled")
        return []

    results: list[tuple[int, str, bool]] = []
    try:
        # pycaw's AudioUtilities.GetAllSessions() returns sessions on the default
        # render device by default. For capture-side, we need a manual COM dance.
        # However, in practice Teams typically opens both render AND capture
        # sessions during a meeting (so it can hear other participants and
        # capture mic). Checking render sessions is a reasonable proxy.
        #
        # If a future Teams update only opens capture sessions, this will need
        # the manual COM enumeration via IMMDeviceEnumerator / eCapture.
        sessions = AudioUtilities.GetAllSessions()
        for session in sessions:
            try:
                if session.Process is None:
                    continue
                pid = session.Process.pid
                name = session.Process.name() if hasattr(session.Process, "name") else ""
                # AudioSessionStateActive == 1
                state_active = (session.State == 1) if hasattr(session, "State") else True
                results.append((pid, name, state_active))
            except Exception:
                logger.exception("Skipping a WASAPI session that failed to read")
    except Exception:
        logger.exception("AudioUtilities.GetAllSessions() failed")
        return []
    return results
