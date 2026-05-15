from __future__ import annotations

from dataclasses import dataclass

from teams_transcriber.events import EventBus, MeetingDetected, MeetingEnded
from teams_transcriber.meeting_watcher import (
    MeetingWatcher,
    WatcherState,
    WindowInfo,
)


@dataclass
class FakeWindows:
    """Drives current_windows() output by popping lists from .scripted."""

    scripted: list[list[WindowInfo]]

    def __call__(self) -> list[WindowInfo]:
        return self.scripted.pop(0) if self.scripted else []


def _teams_meeting(title: str = "Meeting in progress | Microsoft Teams") -> WindowInfo:
    return WindowInfo(pid=123, process_name="ms-teams.exe", title=title)


def _no_match() -> WindowInfo:
    return WindowInfo(pid=99, process_name="explorer.exe", title="File Explorer")


def test_emits_meeting_detected_after_debounce() -> None:
    bus = EventBus()
    received: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, received.append)

    fw = FakeWindows(scripted=[
        [_no_match()],               # tick 1: IDLE -> IDLE
        [_teams_meeting()],          # tick 2: IDLE -> CANDIDATE (not yet emitted)
        [_teams_meeting()],          # tick 3: CANDIDATE -> IN_MEETING (emit)
        [_teams_meeting()],          # tick 4: IN_MEETING -> IN_MEETING
    ])
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    w.step()
    w.step()
    assert received == []
    w.step()
    assert len(received) == 1
    assert received[0].window_title == "Meeting in progress | Microsoft Teams"


def test_emits_meeting_ended_after_window_disappears() -> None:
    bus = EventBus()
    ended: list[MeetingEnded] = []
    bus.subscribe(MeetingEnded, ended.append)

    fw = FakeWindows(scripted=[
        [_teams_meeting()],          # CANDIDATE
        [_teams_meeting()],          # IN_MEETING (emits start)
        [_no_match()],               # LEAVING (not yet emit end)
        [_no_match()],               # IDLE (emit end)
    ])
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    w.step()
    w.step()
    assert ended == []
    w.step()
    assert ended == []
    w.step()
    assert len(ended) == 1


def test_flicker_does_not_emit() -> None:
    """One-tick title flicker (CANDIDATE then back to nothing) doesn't fire."""
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[
        [_no_match()],
        [_teams_meeting()],     # IDLE -> CANDIDATE
        [_no_match()],          # CANDIDATE -> IDLE (no emit)
        [_no_match()],
    ])
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    for _ in range(4):
        w.step()
    assert detected == []


def test_substring_matching() -> None:
    """Configured patterns match as case-insensitive substrings of window title."""
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[
        [WindowInfo(pid=1, process_name="ms-teams.exe",
                    title="Brian's daily call | Microsoft Teams Call")],
    ] * 3)
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["| Microsoft Teams Call"],
        debounce_polls=2,
    )
    for _ in range(3):
        w.step()
    assert len(detected) == 1


def test_non_teams_process_is_ignored() -> None:
    """A window with a matching title but the wrong process must not trigger."""
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[
        [WindowInfo(pid=1, process_name="notepad.exe",
                    title="Meeting in progress | Microsoft Teams - notes.txt")],
    ] * 3)
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    for _ in range(3):
        w.step()
    assert detected == []


def test_pause_blocks_emission() -> None:
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[[_teams_meeting()]] * 3)
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    w.set_paused(True)
    for _ in range(3):
        w.step()
    assert detected == []
    assert w.state == WatcherState.IDLE


def test_resume_after_pause_can_detect() -> None:
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[[_teams_meeting()]] * 6)
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        debounce_polls=2,
    )
    w.set_paused(True)
    for _ in range(3):
        w.step()
    w.set_paused(False)
    for _ in range(3):
        w.step()
    assert len(detected) == 1


# --- Smart detection (denylist of nav views) ------------------------------

def test_scheduled_meeting_subject_is_detected() -> None:
    """A scheduled meeting opens its own window with the meeting subject as title."""
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[
        [WindowInfo(pid=1, process_name="ms-teams.exe",
                    title="Potter // House of Blues - Reception | Microsoft Teams")],
    ] * 3)
    w = MeetingWatcher(
        bus=bus,
        current_windows=fw,
        title_patterns=[],
        debounce_polls=2,
    )
    for _ in range(3):
        w.step()
    assert len(detected) == 1
    assert "Potter" in detected[0].window_title


def test_calendar_view_is_not_detected() -> None:
    """The Calendar nav view must NOT trigger detection even with smart matching."""
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[
        [WindowInfo(pid=1, process_name="ms-teams.exe",
                    title="Calendar | Calendar | Microsoft Teams")],
    ] * 3)
    w = MeetingWatcher(
        bus=bus, current_windows=fw,
        title_patterns=[], debounce_polls=2,
    )
    for _ in range(3):
        w.step()
    assert detected == []


def test_other_nav_views_are_not_detected() -> None:
    """Activity, Chats, Teams, Files, Calls navigation views must not trigger."""
    for nav_view in ["Activity", "Chats", "Teams", "Files", "Calls", "Apps"]:
        bus = EventBus()
        detected: list[MeetingDetected] = []
        bus.subscribe(MeetingDetected, detected.append)
        fw = FakeWindows(scripted=[
            [WindowInfo(pid=1, process_name="ms-teams.exe",
                        title=f"{nav_view} | Microsoft Teams")],
        ] * 3)
        w = MeetingWatcher(
            bus=bus, current_windows=fw,
            title_patterns=[], debounce_polls=2,
        )
        for _ in range(3):
            w.step()
        assert detected == [], f"falsely detected on nav view {nav_view!r}: {detected}"


def test_chat_conversation_is_not_detected() -> None:
    """A chat conversation window (e.g. 'Chat | Alice, Bob | Microsoft Teams') must NOT trigger."""
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)
    fw = FakeWindows(scripted=[
        [WindowInfo(pid=1, process_name="ms-teams.exe",
                    title="Chat | Blake Tyler, Whitney Porto | Microsoft Teams")],
    ] * 3)
    w = MeetingWatcher(
        bus=bus, current_windows=fw,
        title_patterns=[], debounce_polls=2,
    )
    for _ in range(3):
        w.step()
    assert detected == []


def test_calendar_subpage_is_not_detected() -> None:
    """A calendar sub-page (e.g. 'Calendar | My Schedule | Microsoft Teams') must NOT trigger."""
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)
    fw = FakeWindows(scripted=[
        [WindowInfo(pid=1, process_name="ms-teams.exe",
                    title="Calendar | My schedule | Microsoft Teams")],
    ] * 3)
    w = MeetingWatcher(
        bus=bus, current_windows=fw,
        title_patterns=[], debounce_polls=2,
    )
    for _ in range(3):
        w.step()
    assert detected == []


def test_explicit_pattern_still_works_alongside_smart_detection() -> None:
    """If a title matches an explicit pattern, that wins over the denylist."""
    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    fw = FakeWindows(scripted=[
        [WindowInfo(pid=1, process_name="ms-teams.exe",
                    title="Meeting with Brian | Microsoft Teams")],
    ] * 3)
    w = MeetingWatcher(
        bus=bus, current_windows=fw,
        title_patterns=["Meeting with "],
        debounce_polls=2,
    )
    for _ in range(3):
        w.step()
    assert len(detected) == 1


def test_enumerate_windows_returns_list_on_windows() -> None:
    """Smoke: real enumeration returns *some* windows on a real OS.

    We don't assert specific contents — the test just confirms the call doesn't
    raise and returns the right shape. CI on non-Windows will skip this test.
    """
    import sys

    if not sys.platform.startswith("win"):
        import pytest
        pytest.skip("Win32 enumeration is Windows-only")

    from teams_transcriber.meeting_watcher import enumerate_windows

    windows = enumerate_windows()
    assert isinstance(windows, list)
    if windows:
        w = windows[0]
        assert isinstance(w.pid, int)
        assert isinstance(w.process_name, str)
        assert isinstance(w.title, str)
