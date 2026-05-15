"""Post-recording transcription using faster-whisper.

This is the simplest possible shape: take a finalized recording, transcribe the whole
file in one pass, write segments. Live per-channel transcription is a Phase 2.5 follow-up.

Segments are emitted with `channel='others'` because we transcribe the mixed file;
proper per-channel labeling will land alongside live mode.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from teams_transcriber.config import Settings
from teams_transcriber.events import EventBus, TranscriptionComplete
from teams_transcriber.storage import (
    Channel,
    Database,
    RecordingRepo,
    RecordingStatus,
    TranscriptRepo,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

ModelFactory = Callable[..., Any]  # returns a WhisperModel-like object


def _default_model_factory(model_name: str, *, compute_type: str) -> Any:
    from faster_whisper import WhisperModel
    return WhisperModel(model_name, compute_type=compute_type)


class Transcriber:
    def __init__(
        self,
        *,
        bus: EventBus,
        db: Database,
        settings: Settings,
        model_factory: ModelFactory = _default_model_factory,
    ) -> None:
        self._bus = bus
        self._db = db
        self._settings = settings
        self._model_factory = model_factory
        self._model: Any = None

    def transcribe(self, recording_id: int) -> None:
        """Synchronous: transcribe and write segments, then update status + emit event."""
        rec_repo = RecordingRepo(self._db)
        rec = rec_repo.get(recording_id)
        if rec is None:
            logger.error("transcribe(%d): no such recording", recording_id)
            return
        if rec.audio_path is None or not Path(rec.audio_path).exists():
            msg = f"audio file missing: {rec.audio_path}"
            logger.error(msg)
            rec_repo.update_status(
                recording_id, RecordingStatus.TRANSCRIPTION_FAILED, error_message=msg,
            )
            return

        try:
            if self._model is None:
                self._model = self._model_factory(
                    self._settings.transcription_model,
                    compute_type=self._settings.transcription_compute_type,
                )
            segments_iter, _info = self._model.transcribe(
                rec.audio_path,
                language=self._settings.transcription_language,
                vad_filter=True,
            )
            ts_repo = TranscriptRepo(self._db)
            count = 0
            batch: list[TranscriptSegment] = []
            for seg in segments_iter:
                batch.append(TranscriptSegment(
                    id=None,
                    recording_id=recording_id,
                    start_ms=int(seg.start * 1000),
                    end_ms=int(seg.end * 1000),
                    channel=Channel.OTHERS,
                    text=seg.text.strip(),
                ))
                count += 1
                if len(batch) >= 32:
                    ts_repo.append_many(batch)
                    batch.clear()
            if batch:
                ts_repo.append_many(batch)

            rec_repo.update_status(recording_id, RecordingStatus.SUMMARIZING)
            self._bus.publish(TranscriptionComplete(
                recording_id=recording_id, segment_count=count,
            ))
        except Exception as exc:
            logger.exception("transcription failed for recording %d", recording_id)
            rec_repo.update_status(
                recording_id,
                RecordingStatus.TRANSCRIPTION_FAILED,
                error_message=str(exc),
            )
