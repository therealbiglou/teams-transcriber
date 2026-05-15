"""Polls Teams windows and emits MeetingDetected / MeetingEnded events.

This module is split in two:
  * The state-machine + filter logic (here) is pure Python and fully tested.
  * The Win32 window-enumeration (`enumerate_windows`, added in Task 5) is the
    only OS-bound piece.

The polling loop runs in `MeetingWatcher.run_forever`, which calls `step()` every
`poll_interval_ms`. Tests drive `step()` directly with a scripted `current_windows`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

from teams_transcriber.events import EventBus, MeetingDetected, MeetingEnded

logger = logging.getLogger(__name__)

TEAMS_PROCESS_NAMES: frozenset[str] = frozenset({"ms-teams.exe"})


@dataclass(slots=True, frozen=True)
class WindowInfo:
    pid: int
    process_name: str  # lowercased exe name, e.g. "ms-teams.exe"
    title: str


class WatcherState(Enum):
    IDLE = auto()
    CANDIDATE = auto()
    IN_MEETING = auto()
    LEAVING = auto()


class MeetingWatcher:
    """Stateful poller. Drive via `step()` (tests) or `run_forever()` (production)."""

    def __init__(
        self,
        bus: EventBus,
        current_windows: Callable[[], list[WindowInfo]],
        title_patterns: list[str],
        debounce_polls: int = 2,
        poll_interval_ms: int = 2000,
    ) -> None:
        self._bus = bus
        self._current_windows = current_windows
        self._title_patterns = [p.lower() for p in title_patterns]
        self._debounce = max(1, debounce_polls)
        self._poll_interval_s = poll_interval_ms / 1000.0
        self.state = WatcherState.IDLE
        self._consecutive_match = 0
        self._consecutive_miss = 0
        self._paused = False
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused
            if paused:
                # Reset machine so resume starts fresh.
                self.state = WatcherState.IDLE
                self._consecutive_match = 0
                self._consecutive_miss = 0

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def step(self) -> None:
        """One poll cycle. Public for testability."""
        if self.is_paused():
            return

        try:
            windows = self._current_windows()
        except Exception:
            logger.exception("current_windows() raised; treating as no-match")
            windows = []

        match = self._find_meeting_window(windows)

        if match is not None:
            self._consecutive_match += 1
            self._consecutive_miss = 0
        else:
            self._consecutive_miss += 1
            self._consecutive_match = 0

        self._advance(match)

    def run_forever(self) -> None:
        """Production loop. Returns only after stop() is called."""
        while not self._stop.is_set():
            start = time.monotonic()
            self.step()
            elapsed = time.monotonic() - start
            self._stop.wait(timeout=max(0.0, self._poll_interval_s - elapsed))

    def stop(self) -> None:
        self._stop.set()

    # --- internals ---------------------------------------------------------

    def _find_meeting_window(self, windows: list[WindowInfo]) -> WindowInfo | None:
        for w in windows:
            if w.process_name.lower() not in TEAMS_PROCESS_NAMES:
                continue
            title_lower = w.title.lower()
            if any(p in title_lower for p in self._title_patterns):
                return w
        return None

    def _advance(self, match: WindowInfo | None) -> None:
        if self.state == WatcherState.IDLE:
            if self._consecutive_match >= 1:
                self.state = WatcherState.CANDIDATE
                if self._consecutive_match >= self._debounce and match is not None:
                    self._enter_meeting(match)
        elif self.state == WatcherState.CANDIDATE:
            if self._consecutive_match >= self._debounce and match is not None:
                self._enter_meeting(match)
            elif self._consecutive_miss >= 1:
                self.state = WatcherState.IDLE
        elif self.state == WatcherState.IN_MEETING:
            if self._consecutive_miss >= 1:
                self.state = WatcherState.LEAVING
                if self._consecutive_miss >= self._debounce:
                    self._leave_meeting()
        elif self.state == WatcherState.LEAVING:
            if self._consecutive_miss >= self._debounce:
                self._leave_meeting()
            elif self._consecutive_match >= 1:
                self.state = WatcherState.IN_MEETING

    def _enter_meeting(self, w: WindowInfo) -> None:
        self.state = WatcherState.IN_MEETING
        self._bus.publish(MeetingDetected(window_title=w.title))

    def _leave_meeting(self) -> None:
        self.state = WatcherState.IDLE
        self._consecutive_match = 0
        self._consecutive_miss = 0
        self._bus.publish(MeetingEnded())
