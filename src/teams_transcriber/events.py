"""Process-local event bus and event dataclasses.

Phase 2 deliberately uses a plain Python pub/sub (not Qt signals) so the headless
pipeline does not depend on PySide6 or a QApplication. Phase 3 can either bridge
EventBus -> Qt signals or replace the bus.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

# --- Event dataclasses ----------------------------------------------------


@dataclass(slots=True, frozen=True)
class Event:
    """Marker base - every event subclass is itself an event type."""


@dataclass(slots=True, frozen=True)
class MeetingDetected(Event):
    window_title: str


@dataclass(slots=True, frozen=True)
class MeetingEnded(Event):
    pass


@dataclass(slots=True, frozen=True)
class RecordingStarted(Event):
    recording_id: int
    audio_path: str


@dataclass(slots=True, frozen=True)
class RecordingFinalized(Event):
    recording_id: int
    duration_ms: int


@dataclass(slots=True, frozen=True)
class RecordingFailed(Event):
    recording_id: int
    error_message: str


@dataclass(slots=True, frozen=True)
class TranscriptionComplete(Event):
    recording_id: int
    segment_count: int


@dataclass(slots=True, frozen=True)
class SummaryReady(Event):
    recording_id: int


# --- EventBus -------------------------------------------------------------

E = TypeVar("E", bound=Event)
Handler = Callable[[E], None]


class EventBus:
    """Synchronous, thread-safe pub/sub keyed by event class.

    Handlers run on whatever thread calls publish(). Exceptions in one handler
    do not abort other handlers - they're logged and swallowed.
    """

    def __init__(self) -> None:
        # Stored as Callable[[Any], None] internally so we can hold heterogeneous
        # handler signatures in one dict; the public API enforces type safety.
        self._handlers: dict[type[Event], list[Callable[[Any], None]]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event_type: type[E], handler: Handler[E]) -> None:
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: type[E], handler: Handler[E]) -> None:
        with self._lock:
            handlers = self._handlers.get(event_type)
            if handlers is None:
                return
            with contextlib.suppress(ValueError):
                handlers.remove(handler)

    def publish(self, event: Event) -> None:
        with self._lock:
            handlers = list(self._handlers.get(type(event), ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("event handler %r raised on %r", handler, event)
