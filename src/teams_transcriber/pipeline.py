"""Wires EventBus + components into a runnable headless pipeline."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from teams_transcriber.audio.source import AudioSource
from teams_transcriber.config import Settings
from teams_transcriber.events import (
    EventBus,
    MeetingDetected,
    MeetingEnded,
    RecordingFailed,
    RecordingFinalized,
    TranscriptionComplete,
)
from teams_transcriber.meeting_watcher import MeetingWatcher
from teams_transcriber.paths import AppPaths
from teams_transcriber.recorder import Recorder
from teams_transcriber.storage import Database
from teams_transcriber.summarizer import Summarizer
from teams_transcriber.transcriber import Transcriber

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        *,
        bus: EventBus,
        db: Database,
        paths: AppPaths,
        settings: Settings,
        audio_source_factory: Callable[[], AudioSource],
        meeting_watcher: MeetingWatcher | None = None,
        transcriber: Transcriber | None = None,
        summarizer: Summarizer | None = None,
    ) -> None:
        self._bus = bus
        self._db = db
        self._paths = paths
        self._settings = settings
        self._audio_source_factory = audio_source_factory
        self._recorder: Recorder | None = None
        self._transcriber = transcriber or Transcriber(bus=bus, db=db, settings=settings)
        self._summarizer = summarizer or Summarizer(bus=bus, db=db, settings=settings)
        self._meeting_watcher = meeting_watcher  # may be None for manual-only mode
        self._watcher_thread: threading.Thread | None = None
        self._wire()

    # --- public lifecycle ----------------------------------------------

    def start_manual(self, *, detected_title: str | None = None) -> int:
        return self._start_recorder(source_type="manual", detected_title=detected_title)

    def stop_manual(self) -> None:
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None

    def serve(self) -> None:
        if self._meeting_watcher is None:
            raise RuntimeError("Pipeline configured without a MeetingWatcher")
        self._watcher_thread = threading.Thread(
            target=self._meeting_watcher.run_forever, daemon=True, name="watcher",
        )
        self._watcher_thread.start()

    def shutdown(self) -> None:
        if self._meeting_watcher is not None:
            self._meeting_watcher.stop()
        if self._watcher_thread is not None:
            self._watcher_thread.join(timeout=3.0)
        if self._recorder is not None:
            self._recorder.stop()

    # --- wiring --------------------------------------------------------

    def _wire(self) -> None:
        self._bus.subscribe(MeetingDetected, self._on_meeting_detected)
        self._bus.subscribe(MeetingEnded, self._on_meeting_ended)
        self._bus.subscribe(RecordingFinalized, self._on_recording_finalized)
        self._bus.subscribe(RecordingFailed, self._on_recording_failed)
        self._bus.subscribe(TranscriptionComplete, self._on_transcription_complete)

    def _on_meeting_detected(self, evt: MeetingDetected) -> None:
        try:
            self._start_recorder(source_type="teams", detected_title=evt.window_title)
        except Exception:
            logger.exception("failed to start recorder for %r", evt.window_title)

    def _on_meeting_ended(self, _evt: MeetingEnded) -> None:
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None

    def _on_recording_finalized(self, evt: RecordingFinalized) -> None:
        try:
            self._transcriber.transcribe(evt.recording_id)
        except Exception:
            logger.exception("transcription crashed for %d", evt.recording_id)

    def _on_recording_failed(self, evt: RecordingFailed) -> None:
        logger.warning("recording %d failed: %s", evt.recording_id, evt.error_message)
        # Worker thread already updated status; just release the slot.
        self._recorder = None

    def _on_transcription_complete(self, evt: TranscriptionComplete) -> None:
        try:
            self._summarizer.summarize(
                evt.recording_id, api_key=self._settings.anthropic_api_key(),
            )
        except Exception:
            logger.exception("summarization crashed for %d", evt.recording_id)

    def _start_recorder(self, *, source_type: str, detected_title: str | None) -> int:
        if self._recorder is not None:
            logger.warning("recorder already running; ignoring duplicate start")
            return -1
        source = self._audio_source_factory()
        self._recorder = Recorder(
            bus=self._bus, db=self._db, paths=self._paths,
            settings=self._settings, audio_source=source,
        )
        return self._recorder.start(source_type=source_type, detected_title=detected_title)
