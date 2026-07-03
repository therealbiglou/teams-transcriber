"""Wires EventBus + components into a runnable headless pipeline."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np

from teams_transcriber.audio.source import AudioSource, NoAudioDevicesError
from teams_transcriber.config import Settings
from teams_transcriber.events import (
    EventBus,
    MeetingDetected,
    MeetingEnded,
    RecordingDeviceFallback,
    RecordingFailed,
    RecordingFinalized,
    SummaryFailed,
    TranscriptionComplete,
    TranscriptionFailed,
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
        processing_gate: Callable[[int], bool] | None = None,
    ) -> None:
        self._bus = bus
        self._db = db
        self._paths = paths
        self._settings = settings
        self._audio_source_factory = audio_source_factory
        self._processing_gate = processing_gate
        self._deferred: dict[int, RecordingFinalized] = {}
        self._release_requested: set[int] = set()
        self._defer_lock = threading.Lock()
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
        """Re-run summarization on the post-processing executor. Never on the
        caller thread — the UI invokes this from a Qt slot and the Anthropic
        call would freeze the event loop."""
        self._submit_summarize(recording_id, api_key=api_key)

    def import_audio_file(self, src_path: str) -> int:
        """Import an external audio file as a recording and start processing.

        Returns the new recording's id. The file is copied into the audio dir
        (consistent with native recordings), a Recording row is created at
        status TRANSCRIBING, and post-processing (transcribe + summarize) is
        enqueued. Raises FileNotFoundError / ValueError from the importer on a
        missing or non-audio file.
        """
        from pathlib import Path
        from teams_transcriber.audio.importer import import_audio_file
        rid = import_audio_file(Path(src_path), db=self._db, paths=self._paths)
        self._submit_post_processing(rid)
        return rid

    def import_transcript_file(self, src_path: str) -> int:
        """Import an external transcript file (.txt/.md/.vtt/.srt) and summarize.

        Returns the new recording's id. The file's text becomes a single
        transcript segment; status starts at TRANSCRIBING and the existing
        Transcriber path skips Whisper because the segment already covers the
        recording's duration. Summarization then fires normally.
        """
        from pathlib import Path
        from teams_transcriber.transcript_importer import import_transcript_file
        rid = import_transcript_file(Path(src_path), db=self._db, paths=self._paths)
        self._submit_post_processing(rid)
        return rid

    def retry_transcription(self, recording_id: int) -> None:
        """Public entry point for re-running transcription (and onward).

        Resets the recording's status to TRANSCRIBING and enqueues
        _run_post_processing on the executor — the same code path the
        normal RecordingFinalized handler uses. Safe to call on any
        recording regardless of its current failed/done status.
        """
        rec_repo = RecordingRepo(self._db)
        rec = rec_repo.get(recording_id)
        if rec is None:
            return
        rec_repo.update_status(
            recording_id,
            RecordingStatus.TRANSCRIBING,
            error_message=None,
        )
        self._submit_post_processing(recording_id)

    def release_processing(self, recording_id: int) -> None:
        """Resume deferred post-processing (called when the notes window closes)."""
        with self._defer_lock:
            evt = self._deferred.pop(recording_id, None)
            if evt is None:
                # Release may have arrived before _on_recording_finalized stored the
                # deferral (race). Mark it so the finalize handler won't strand it.
                self._release_requested.add(recording_id)
                return
        RecordingRepo(self._db).update_status(recording_id, RecordingStatus.TRANSCRIBING)
        self._submit_post_processing(recording_id)

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

    def _submit_post_processing(self, recording_id: int) -> None:
        future = self._executor.submit(self._run_post_processing, recording_id)
        self._pending_futures.append(future)

    def _submit_summarize(self, recording_id: int, *, api_key: str | None) -> None:
        """Submit summarize() on the executor and log (rather than swallow) any
        unexpected raise that would otherwise die silently in the Future."""
        future = self._executor.submit(
            self._summarizer.summarize, recording_id, api_key=api_key,
        )
        future.add_done_callback(
            lambda f, rid=recording_id: (
                logger.exception("summarize task for %d crashed", rid, exc_info=f.exception())
                if f.exception() is not None else None
            )
        )
        self._pending_futures.append(future)

    def _on_recording_finalized(self, evt: RecordingFinalized) -> None:
        with self._defer_lock:
            # Evaluate the gate inside the lock so defer and release_processing
            # are serialized — this closes the TOCTOU race where a release that
            # arrives between the gate read and the store would be lost.
            gated = (
                self._processing_gate is not None
                and self._processing_gate(evt.recording_id)
                and evt.recording_id not in self._release_requested
            )
            if gated:
                RecordingRepo(self._db).update_status(
                    evt.recording_id, RecordingStatus.WAITING_FOR_NOTES,
                )
                self._deferred[evt.recording_id] = evt
                logger.info(
                    "deferring post-processing for %d (notes window open)", evt.recording_id,
                )
                return
            # Not deferring (gate false, or a release already arrived early):
            # clear any early-release marker so it can't leak to a future recording.
            self._release_requested.discard(evt.recording_id)
        self._submit_post_processing(evt.recording_id)

    def _on_recording_failed(self, evt: RecordingFailed) -> None:
        logger.warning("recording %d failed: %s", evt.recording_id, evt.error_message)
        # Worker thread already updated status; just release the slot.
        self._recorder = None
        if self._live_transcriber is not None:
            self._live_transcriber.flush_and_stop()
            self._live_transcriber = None

    def _on_transcription_complete(self, evt: TranscriptionComplete) -> None:
        try:
            self._summarizer.summarize(
                evt.recording_id, api_key=self._settings.anthropic_api_key(),
            )
        except Exception as exc:
            logger.exception("summarization crashed for %d", evt.recording_id)
            try:
                rec_repo = RecordingRepo(self._db)
                rec_repo.update_status(
                    evt.recording_id,
                    RecordingStatus.SUMMARY_FAILED,
                    error_message=f"unexpected error: {exc}",
                )
            except Exception:
                logger.exception("could not even update status for %d", evt.recording_id)
            self._bus.publish(SummaryFailed(
                recording_id=evt.recording_id,
                error_message=f"unexpected error: {exc}",
            ))

    def _run_post_processing(self, recording_id: int) -> None:
        try:
            self._transcriber.transcribe(recording_id)
        except Exception as exc:
            logger.exception("transcription crashed for %d", recording_id)
            # Defensive: ensure the UI hears about it even if Transcriber's
            # internal try/except didn't fire.
            try:
                rec_repo = RecordingRepo(self._db)
                rec_repo.update_status(
                    recording_id,
                    RecordingStatus.TRANSCRIPTION_FAILED,
                    error_message=f"unexpected error: {exc}",
                )
            except Exception:
                logger.exception("could not even update status for %d", recording_id)
            self._bus.publish(TranscriptionFailed(
                recording_id=recording_id,
                error_message=f"unexpected error: {exc}",
            ))
        # transcribe() publishes TranscriptionComplete synchronously inside its body;
        # _on_transcription_complete runs on this same worker thread.

    def _recover_stuck_recordings(self) -> None:
        """At startup, transition stuck rows to a correct state.

        - SUMMARIZING + has Summary row → DONE (summary clearly succeeded;
          summarizer crashed between sum_repo.upsert and update_status).
        - SUMMARIZING + no Summary → SUMMARY_FAILED.
        - TRANSCRIBING + has segments → SUMMARIZING.
        - TRANSCRIBING + no segments → TRANSCRIPTION_FAILED.
        """
        from teams_transcriber.storage import SummaryRepo, TranscriptRepo

        rec_repo = RecordingRepo(self._db)
        sum_repo = SummaryRepo(self._db)
        tr_repo = TranscriptRepo(self._db)

        for rec in rec_repo.list_by_status(RecordingStatus.SUMMARIZING):
            if rec.id is None:
                continue
            if sum_repo.get(rec.id) is not None:
                logger.info("recover: %d had summary, marking DONE", rec.id)
                rec_repo.update_status(rec.id, RecordingStatus.DONE)
                continue
            logger.warning("recovering stuck SUMMARIZING %d (no summary)", rec.id)
            rec_repo.update_status(
                rec.id, RecordingStatus.SUMMARY_FAILED,
                error_message="summary was interrupted (app exited mid-process)",
            )

        for rec in rec_repo.list_by_status(RecordingStatus.TRANSCRIBING):
            if rec.id is None:
                continue
            segments = tr_repo.list_for_recording(rec.id)
            if segments:
                logger.info("recover: %d had segments, resuming summarization", rec.id)
                rec_repo.update_status(rec.id, RecordingStatus.SUMMARIZING)
                self._submit_summarize(rec.id, api_key=self._settings.anthropic_api_key())
                continue
            logger.warning("recovering stuck TRANSCRIBING %d (no segments)", rec.id)
            rec_repo.update_status(
                rec.id, RecordingStatus.TRANSCRIPTION_FAILED,
                error_message="transcription was interrupted (app exited mid-process)",
            )

        for rec in rec_repo.list_by_status(RecordingStatus.WAITING_FOR_NOTES):
            if rec.id is None:
                continue
            # No notes window can be open at startup — process it now.
            logger.info("recover: %d was waiting for notes, resuming", rec.id)
            rec_repo.update_status(rec.id, RecordingStatus.TRANSCRIBING)
            self._submit_post_processing(rec.id)

    def _start_recorder(self, *, source_type: str, detected_title: str | None) -> int:
        if self._recorder is not None:
            logger.warning("recorder already running; ignoring duplicate start")
            return -1

        try:
            source = self._audio_source_factory()
        except NoAudioDevicesError as exc:
            logger.warning("recording start failed: %s", exc)
            self._bus.publish(RecordingFailed(
                recording_id=-1,
                error_message=str(exc),
            ))
            return -1
        except Exception as exc:
            logger.exception("audio source factory failed")
            self._bus.publish(RecordingFailed(
                recording_id=-1,
                error_message=f"Audio capture could not start: {exc}",
            ))
            return -1

        live = None
        audio_chunk_callback = None
        if self._settings.transcription_live_enabled:
            live = LiveTranscriber(
                bus=self._bus, db=self._db, settings=self._settings,
            )
            self._live_transcriber = live

            def _on_audio_chunk(chunk: np.ndarray) -> None:
                mic = chunk[:, 0]
                loop = chunk[:, 1]
                live.feed(Channel.ME, mic)
                live.feed(Channel.OTHERS, loop)
            audio_chunk_callback = _on_audio_chunk

        self._recorder = Recorder(
            bus=self._bus, db=self._db, paths=self._paths,
            settings=self._settings, audio_source=source,
            audio_chunk_callback=audio_chunk_callback,
        )
        rec_id = self._recorder.start(
            source_type=source_type, detected_title=detected_title,
        )

        if rec_id > 0:
            # Republish any device-fallbacks the source recorded during construction.
            for channel, requested_name in getattr(source, "device_fallbacks", []):
                self._bus.publish(RecordingDeviceFallback(
                    recording_id=rec_id,
                    channel=channel,
                    requested_name=requested_name,
                ))
            if live is not None:
                live.start(rec_id)
        return rec_id
