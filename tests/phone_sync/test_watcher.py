"""PhoneSyncWatcher: pure threading, no Qt. Uses threading.Event gates and
generous timeouts (poll_seconds=0.05) instead of blind sleeps for the
outcomes under test; a couple of short sleeps are used only to observe an
*absence* of extra calls over several poll cycles."""

from __future__ import annotations

import threading
import time

from teams_transcriber.phone_sync.mtp import MtpNotReady
from teams_transcriber.phone_sync.watcher import PhoneSyncWatcher


def test_arrival_triggers_one_cycle_and_rearms_only_after_departure():
    """False->True triggers exactly one run_cycle; staying True doesn't
    re-trigger; a False->True after departure re-triggers a second cycle."""
    presence = [False]
    presence_lock = threading.Lock()
    cycle_calls: list[int] = []
    cycle_ran = threading.Event()

    def probe() -> bool:
        with presence_lock:
            return presence[0]

    def run_cycle() -> None:
        cycle_calls.append(1)
        cycle_ran.set()

    watcher = PhoneSyncWatcher(run_cycle=run_cycle, probe=probe, poll_seconds=0.05)
    watcher.start()
    try:
        with presence_lock:
            presence[0] = True
        assert cycle_ran.wait(timeout=2), "run_cycle never fired on arrival"

        # Several more polls while probe stays True -- must not re-trigger.
        time.sleep(0.3)
        assert cycle_calls == [1]

        # Depart, then re-arrive -> a second, distinct cycle.
        cycle_ran.clear()
        with presence_lock:
            presence[0] = False
        time.sleep(0.2)
        with presence_lock:
            presence[0] = True
        assert cycle_ran.wait(timeout=2), "run_cycle never re-fired on re-arrival"
        assert cycle_calls == [1, 1]
    finally:
        watcher.stop()


def test_stop_joins_promptly_even_mid_poll():
    """stop() must not block for anywhere near the poll interval."""
    watcher = PhoneSyncWatcher(
        run_cycle=lambda: None, probe=lambda: False, poll_seconds=5.0,
    )
    watcher.start()
    time.sleep(0.05)  # let the thread land inside its 5s wait()
    started = time.monotonic()
    watcher.stop()
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"stop() took {elapsed:.2f}s to join"


def test_device_not_ready_reports_hint_once_per_arrival_and_skips_cycle():
    """A persistent 'device_not_ready' probe (staged arrival -- phone locked
    or wrong USB mode) must call on_error exactly once, and run_cycle must
    never run since the device never becomes truly present."""
    hints: list[str] = []
    hint_seen = threading.Event()
    cycle_calls: list[int] = []

    def probe() -> bool:
        raise MtpNotReady("device_not_ready", hint="Unlock the phone and set USB to File transfer.")

    def on_error(hint: str) -> None:
        hints.append(hint)
        hint_seen.set()

    watcher = PhoneSyncWatcher(
        run_cycle=lambda: cycle_calls.append(1),
        probe=probe,
        poll_seconds=0.05,
        on_error=on_error,
    )
    watcher.start()
    try:
        assert hint_seen.wait(timeout=2), "on_error never fired for device_not_ready"
        time.sleep(0.3)  # several more polls; still stuck in the same state
        assert hints == ["Unlock the phone and set USB to File transfer."]
        assert cycle_calls == []
    finally:
        watcher.stop()


def test_run_cycle_exception_consumes_arrival_and_rearms_after_departure():
    """A raising run_cycle still consumes the arrival: on_error gets the
    failure text, there is NO retry while the probe stays True, and only a
    departure + re-arrival produces a new run_cycle attempt."""
    presence = [False]
    presence_lock = threading.Lock()
    cycle_calls: list[int] = []
    first_attempt = threading.Event()
    second_attempt = threading.Event()
    errors: list[str] = []
    error_seen = threading.Event()

    def probe() -> bool:
        with presence_lock:
            return presence[0]

    def run_cycle() -> None:
        cycle_calls.append(1)
        if len(cycle_calls) == 1:
            first_attempt.set()
            raise RuntimeError("sync blew up")
        second_attempt.set()

    def on_error(msg: str) -> None:
        errors.append(msg)
        error_seen.set()

    watcher = PhoneSyncWatcher(
        run_cycle=run_cycle, probe=probe, poll_seconds=0.05, on_error=on_error,
    )
    watcher.start()
    try:
        with presence_lock:
            presence[0] = True
        assert first_attempt.wait(timeout=2), "run_cycle never attempted on arrival"
        assert error_seen.wait(timeout=2), "on_error never fired for the failed cycle"
        assert errors == ["Phone sync failed — see logs."]

        # Probe stays True over several more polls: the failed arrival is
        # consumed -- no retry.
        time.sleep(0.3)
        assert cycle_calls == [1]

        # Depart, then re-arrive -> a fresh run_cycle attempt.
        with presence_lock:
            presence[0] = False
        time.sleep(0.2)
        with presence_lock:
            presence[0] = True
        assert second_attempt.wait(timeout=2), "no new attempt after re-arrival"
        assert cycle_calls == [1, 1]
    finally:
        watcher.stop()


def test_arbitrary_probe_exception_is_absent_and_recovers():
    """A plain RuntimeError from probe (not MtpNotReady) is treated as
    absent: run_cycle never runs, on_error is never called (the watcher
    logs it instead -- on_error is reserved for the device_not_ready hint
    and run_cycle failures), and once the probe heals to a plain True the
    arrival still triggers a cycle."""
    mode = ["raise"]
    mode_lock = threading.Lock()
    cycle_calls: list[int] = []
    cycle_ran = threading.Event()
    errors: list[str] = []
    raise_count = [0]
    raised_enough = threading.Event()

    def probe() -> bool:
        with mode_lock:
            current = mode[0]
        if current == "raise":
            raise_count[0] += 1
            if raise_count[0] >= 3:
                raised_enough.set()
            raise RuntimeError("COM exploded")
        return True

    def run_cycle() -> None:
        cycle_calls.append(1)
        cycle_ran.set()

    watcher = PhoneSyncWatcher(
        run_cycle=run_cycle, probe=probe, poll_seconds=0.05, on_error=errors.append,
    )
    watcher.start()
    try:
        assert raised_enough.wait(timeout=2), "probe never polled 3 times"
        assert cycle_calls == []
        assert errors == []  # generic probe failures are logged, not on_error'd

        # Probe heals -> this is a False(-ish)->True transition -> one cycle.
        with mode_lock:
            mode[0] = "ok"
        assert cycle_ran.wait(timeout=2), "healthy probe never triggered a cycle"
        assert cycle_calls == [1]
        assert errors == []
    finally:
        watcher.stop()


def test_other_not_ready_reasons_are_silent_and_absent():
    """no_device/no_marker/service_stopped reasons are 'waiting' states:
    no on_error call, treated as absent (no run_cycle)."""
    cycle_calls: list[int] = []
    errors: list[str] = []
    poll_count = [0]
    polled_enough = threading.Event()

    def probe() -> bool:
        poll_count[0] += 1
        if poll_count[0] >= 3:
            polled_enough.set()
        raise MtpNotReady("no_device", hint="Plug the phone in over USB.")

    watcher = PhoneSyncWatcher(
        run_cycle=lambda: cycle_calls.append(1),
        probe=probe,
        poll_seconds=0.05,
        on_error=errors.append,
    )
    watcher.start()
    try:
        assert polled_enough.wait(timeout=2)
        assert errors == []
        assert cycle_calls == []
    finally:
        watcher.stop()
