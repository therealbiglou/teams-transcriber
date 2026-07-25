"""Background device-arrival watcher: one sync cycle per phone plug-in.

A plain daemon thread that polls a caller-supplied `probe()` at
`poll_seconds` intervals and calls `run_cycle()` exactly once per
False->True transition (device arrival) -- staying plugged in never
re-triggers a cycle; the phone has to depart (probe() False/absent) and
re-arrive for another cycle to run. This mirrors the MTP spike's staged-
arrival hazard (docs/superpowers/specs/2026-07-24-mtp-spike-findings.md
#3): `find_phone_root` (the intended `probe`) raises `MtpNotReady` while
the phone is connected but not yet usable (locked, wrong USB mode, no
storages enumerated), which must not be treated as a hard error on every
poll -- but the `device_not_ready` reason specifically is worth telling
the user about (unlock hint), once per arrival, not once per poll.

Other `MtpNotReady` reasons (`no_device`, `no_marker`, `service_stopped`)
and any unexpected exception from `probe()` are treated as "absent,
silently" -- these are either genuinely no-device states or app-level
conditions the watcher can't act on mid-poll.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from teams_transcriber.phone_sync.mtp import MtpNotReady

logger = logging.getLogger(__name__)


class PhoneSyncWatcher:
    """Polls `probe()`; runs `run_cycle()` once per device arrival."""

    def __init__(
        self,
        *,
        run_cycle: Callable[[], None],
        probe: Callable[[], bool],
        poll_seconds: float = 5.0,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._run_cycle = run_cycle
        self._probe = probe
        self.poll_seconds = poll_seconds
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="PhoneSyncWatcher", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _report_error(self, hint: str) -> None:
        if self._on_error is None:
            return
        try:
            self._on_error(hint)
        except Exception:
            logger.exception("phone-sync on_error callback failed")

    def _run(self) -> None:
        was_present = False
        # True only while stuck in the "device connected but not ready"
        # state -- reset as soon as that specific condition clears (probe
        # succeeds, or a different/no exception), so the next arrival that
        # stalls in the same state gets its own notification.
        errored_this_arrival = False

        while not self._stop.wait(self.poll_seconds):
            present = False
            try:
                present = self._probe()
                errored_this_arrival = False
            except MtpNotReady as exc:
                if exc.reason == "device_not_ready":
                    if not errored_this_arrival:
                        errored_this_arrival = True
                        self._report_error(exc.hint)
                else:
                    errored_this_arrival = False
            except Exception:
                logger.exception("phone-sync probe failed")
                errored_this_arrival = False

            if present and not was_present:
                try:
                    self._run_cycle()
                except Exception:
                    logger.exception("phone-sync run_cycle failed")
                    self._report_error("Phone sync failed — see logs.")
            was_present = present
