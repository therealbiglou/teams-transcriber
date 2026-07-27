"""WASAPI capture-session probe — used by MeetingWatcher to detect "Teams is
holding the mic" without depending on window titles.

All errors (missing pycaw, COM failures, etc.) degrade to an empty-set return.
The watcher falls back to title pattern matching when this returns empty.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

TEAMS_PROCESS_NAMES = {"ms-teams.exe", "teams.exe"}

# The watcher polls this every ~2s. A persistent COM failure (most commonly
# "Element not found" from GetSpeakers() when Windows has no default playback
# device) would otherwise write a full traceback every poll — tens of thousands
# of lines that bury everything else in the log. Report the first failure, then
# stay quiet and re-report only once per interval while it persists.
_REPEAT_LOG_INTERVAL_S = 600.0
_probe_failure: dict[str, float | str | int | None] = {
    "signature": None, "last_logged_at": 0.0, "suppressed": 0,
}


def _note_probe_failure(exc: Exception) -> None:
    """Log an enumeration failure at most once per _REPEAT_LOG_INTERVAL_S."""
    signature = f"{type(exc).__name__}: {exc}"
    now = time.monotonic()
    first_time = _probe_failure["signature"] != signature
    if first_time:
        _probe_failure.update(signature=signature, last_logged_at=now, suppressed=0)
        logger.warning(
            "WASAPI session probe unavailable (%s). Meeting detection falls back to "
            "window titles. Most often this means Windows has no default audio "
            "playback device on this machine. Further identical failures will be "
            "logged at most every %d minutes.",
            signature, int(_REPEAT_LOG_INTERVAL_S // 60),
        )
        return
    _probe_failure["suppressed"] = int(_probe_failure["suppressed"] or 0) + 1
    if now - float(_probe_failure["last_logged_at"] or 0.0) >= _REPEAT_LOG_INTERVAL_S:
        logger.warning(
            "WASAPI session probe still unavailable (%s); %d further failures suppressed.",
            signature, _probe_failure["suppressed"],
        )
        _probe_failure.update(last_logged_at=now, suppressed=0)


def _note_probe_recovered() -> None:
    """Clear failure state, logging once if we were previously failing."""
    if _probe_failure["signature"] is not None:
        logger.info("WASAPI session probe recovered.")
        _probe_failure.update(signature=None, last_logged_at=0.0, suppressed=0)


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
                logger.debug("Skipping a WASAPI session that failed to read", exc_info=True)
    except Exception as exc:
        _note_probe_failure(exc)
        return []
    _note_probe_recovered()
    return results
