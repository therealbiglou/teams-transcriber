"""Wires EventBus + components into a runnable headless pipeline."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np

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
from teams_transcriber.live_transcriber import LiveTranscriber
from teams_transcriber.meeting_watcher import MeetingWatcher
from teams_transcriber.paths import AppPaths
from teams_transcriber.recorder import Recorder
from teams_transcriber.storage import Channel, Database, RecordingRepo, RecordingStatus
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
        self._live_transcriber: LiveTranscriber | None = None
        self._transcriber = transcriber or Transcriber(bus=bus, db=db, settings=settings)
        self._summarizer = summarizer or Summarizer(bus=bus, db=db, settings=settings)
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="post-processing",
        )
        self._pending_futures: list[Future[None]] = []
        self._meeting_watcher = meeting_watcher  # may be None for manual-only mode
        self._watcher_thread: threading.Thread | None = None
        self._wire()
        self._recover_stuck_recordings()

    # --- public lifecycle ----------------------------------------------

    def start_manual(self, *, detected_title: str | None = None) -> int:
        return self._start_recorder(source_type="manual", detected_title=detected_title)

    def stop_manual(self) -> None:
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None
        if self._live_transcriber is not None:
            self._live_transcriber.flush_and_stop()
            self._live_transcriber = None

    def retry_summary(self, recording_id: int, *, api_key: str | None) -> None:
        """Public entry point for re-running summarization on an existing recording."""
        self._summarizer.summarize(recording_id, api_key=api_key)

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
            self._recorder = None
        if self._live_transcriber is not None:
            self._live_transcriber.flush_and_stop()
            self._live_transcriber = None
        self._executor.shutdown(wait=True)

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
        rec = self._recorder
        self._recorder = None
        if rec is not None:
            rec.stop()
        if self._live_transcriber is not None:
            self._live_transcriber.flush_and_stop()
            self._live_transcriber = None

    def _on_recording_finalized(self, evt: RecordingFinalized) -> None:
        future = self._executor.submit(self._run_post_processing, evt.recording_id)
        self._pending_futures.append(future)

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

    def _run_post_processing(self, recording_id: int) -> None:
        try:
            self._transcriber.transcribe(recording_id)
        except Exception:
            logger.exception("transcription crashed for %d", recording_id)
        # transcribe() publishes TranscriptionComplete synchronously inside its body;
        # _on_transcription_complete runs on this same worker thread.

    def _recover_stuck_recordings(self) -> None:
        """At startup, transition any TRANSCRIBING/SUMMARIZING rows to *_FAILED.

        These are recordings whose post-processing was interrupted (app crash,
        forced exit). Leaving them in an in-progress state confuses the UI
        and prevents the user from triggering retry. Mark them failed with a
        clear message so the existing retry path can pick them back up.
        """
        rec_repo = RecordingRepo(self._db)
        for rec in rec_repo.list_by_status(RecordingStatus.TRANSCRIBING):
            if rec.id is None:
                continue
            logger.warning("recovering stuck TRANSCRIBING recording %d", rec.id)
            rec_repo.update_status(
                rec.id, RecordingStatus.TRANSCRIPTION_FAILED,
                error_message="transcription was interrupted (app exited mid-process)",
            )
        for rec in rec_repo.list_by_status(RecordingStatus.SUMMARIZING):
            if rec.id is None:
                continue
            logger.warning("recovering stuck SUMMARIZING recording %d", rec.id)
            rec_repo.update_status(
                rec.id, RecordingStatus.SUMMARY_FAILED,
                error_message="summary was interrupted (app exited mid-process)",
            )

    def _start_recorder(self, *, source_type: str, detected_title: str | None) -> int:
        if self._recorder is not None:
            logger.warning("recorder already running; ignoring duplicate start")
            return -1
        source = self._audio_source_factory()

        live = LiveTranscriber(
            bus=self._bus, db=self._db, settings=self._settings,
        )
        self._live_transcriber = live

        def _on_audio_chunk(chunk: np.ndarray) -> None:
            # chunk shape: (frames, 2) float32. col 0 = mic (ME); col 1 = loopback (OTHERS).
            mic = chunk[:, 0]
            loop = chunk[:, 1]
            live.feed(Channel.ME, mic)
            live.feed(Channel.OTHERS, loop)

        self._recorder = Recorder(
            bus=self._bus, db=self._db, paths=self._paths,
            settings=self._settings, audio_source=source,
            audio_chunk_callback=_on_audio_chunk,
        )
        rec_id = self._recorder.start(source_type=source_type, detected_title=detected_title)
        if rec_id > 0:
            live.start(rec_id)
        return rec_id
