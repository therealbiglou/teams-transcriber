"""Bridges the plain-Python `EventBus` to Qt signals so UI components react safely.

`QtEventBridge` subscribes to every event type from `events.py` and re-emits each
as a Qt signal. Qt auto-queues signal/slot dispatch across thread boundaries, so
the UI handlers always run on the main thread regardless of which thread published.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from teams_transcriber.events import (
    EventBus,
    LiveSegmentAvailable,
    LiveTranscriptionDegraded,
    MeetingDetected,
    MeetingEnded,
    RecordingDeviceFallback,
    RecordingFailed,
    RecordingFinalized,
    RecordingStarted,
    SummaryFailed,
    SummaryReady,
    TranscriptionComplete,
    TranscriptionFailed,
    UpdateAvailable,
    UpdateCheckCompleted,
)


class QtEventBridge(QObject):
    """Re-emits EventBus events as Qt signals on the main thread."""

    meeting_detected = Signal(object)
    meeting_ended = Signal(object)
    recording_started = Signal(object)
    recording_finalized = Signal(object)
    recording_failed = Signal(object)
    recording_device_fallback = Signal(object)
    transcription_complete = Signal(object)
    transcription_failed = Signal(object)
    summary_ready = Signal(object)
    summary_failed = Signal(object)
    live_segment_available = Signal(object)
    live_transcription_degraded = Signal(object)
    update_available = Signal(object)
    update_check_completed = Signal(object)

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = bus

        bus.subscribe(MeetingDetected,        self._on_meeting_detected)
        bus.subscribe(MeetingEnded,           self._on_meeting_ended)
        bus.subscribe(RecordingStarted,       self._on_recording_started)
        bus.subscribe(RecordingFinalized,     self._on_recording_finalized)
        bus.subscribe(RecordingFailed,        self._on_recording_failed)
        bus.subscribe(RecordingDeviceFallback, self._on_recording_device_fallback)
        bus.subscribe(TranscriptionComplete,  self._on_transcription_complete)
        bus.subscribe(TranscriptionFailed,    self._on_transcription_failed)
        bus.subscribe(SummaryReady,           self._on_summary_ready)
        bus.subscribe(SummaryFailed,          self._on_summary_failed)
        bus.subscribe(LiveSegmentAvailable,        self._on_live_segment)
        bus.subscribe(LiveTranscriptionDegraded,   self._on_live_degraded)
        bus.subscribe(UpdateAvailable,             self._on_update_available)
        bus.subscribe(UpdateCheckCompleted,        self._on_update_check_completed)

    def _on_meeting_detected(self, e: MeetingDetected) -> None:        self.meeting_detected.emit(e)
    def _on_meeting_ended(self, e: MeetingEnded) -> None:              self.meeting_ended.emit(e)
    def _on_recording_started(self, e: RecordingStarted) -> None:      self.recording_started.emit(e)
    def _on_recording_finalized(self, e: RecordingFinalized) -> None:  self.recording_finalized.emit(e)
    def _on_recording_failed(self, e: RecordingFailed) -> None:        self.recording_failed.emit(e)
    def _on_recording_device_fallback(self, e: RecordingDeviceFallback) -> None:
        self.recording_device_fallback.emit(e)
    def _on_transcription_complete(self, e: TranscriptionComplete) -> None:
        self.transcription_complete.emit(e)
    def _on_transcription_failed(self, e: TranscriptionFailed) -> None:
        self.transcription_failed.emit(e)
    def _on_summary_ready(self, e: SummaryReady) -> None:              self.summary_ready.emit(e)
    def _on_summary_failed(self, e: SummaryFailed) -> None:           self.summary_failed.emit(e)
    def _on_live_segment(self, e: LiveSegmentAvailable) -> None:
        self.live_segment_available.emit(e)
    def _on_live_degraded(self, e: LiveTranscriptionDegraded) -> None:
        self.live_transcription_degraded.emit(e)
    def _on_update_available(self, e: UpdateAvailable) -> None:
        self.update_available.emit(e)
    def _on_update_check_completed(self, e: UpdateCheckCompleted) -> None:
        self.update_check_completed.emit(e)
