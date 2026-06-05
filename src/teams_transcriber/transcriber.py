"""Per-channel transcription using faster-whisper.

The recorder writes a 2-channel Opus file (mic + loopback). The Transcriber splits
that file into two mono WAVs via PyAV, runs faster-whisper on each, and persists
segments tagged with `Channel.ME` (mic) or `Channel.OTHERS` (loopback). Segments
from both channels are interleaved by start_ms when written to storage.

Live transcription (streaming results as the meeting progresses) is a future
enhancement; today this runs once at end-of-meeting.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from teams_transcriber.config import Settings
from teams_transcriber.events import EventBus, TranscriptionComplete, TranscriptionFailed
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
    LIVE_COVERAGE_THRESHOLD = 0.95

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
        """Finalize-or-recover.

        Fast path: if existing transcript segments already cover >= 95 % of
        the recording's duration, the LiveTranscriber did its job — we just
        advance status to SUMMARIZING and publish TranscriptionComplete.

        Recovery path: otherwise, run the legacy batch path (split the Opus
        to two mono WAVs, transcribe each, merge by start_ms, persist).
        """
        rec_repo = RecordingRepo(self._db)
        rec = rec_repo.get(recording_id)
        if rec is None:
            logger.error("transcribe(%d): no such recording", recording_id)
            return

        existing = TranscriptRepo(self._db).list_for_recording(recording_id)
        duration_ms = rec.duration_ms or 0
        if duration_ms > 0 and existing:
            covered_ms = sum(max(0, s.end_ms - s.start_ms) for s in existing)
            coverage = covered_ms / duration_ms
            if coverage >= self.LIVE_COVERAGE_THRESHOLD:
                logger.info(
                    "transcribe(%d): live coverage %.1f%% — skipping batch",
                    recording_id, coverage * 100,
                )
                rec_repo.update_status(recording_id, RecordingStatus.SUMMARIZING)
                self._bus.publish(TranscriptionComplete(
                    recording_id=recording_id, segment_count=len(existing),
                ))
                return

        # Recovery path — preserve the original implementation.
        if rec.audio_path is None or not Path(rec.audio_path).exists():
            msg = f"audio file missing: {rec.audio_path}"
            logger.error(msg)
            rec_repo.update_status(
                recording_id, RecordingStatus.TRANSCRIPTION_FAILED, error_message=msg,
            )
            self._bus.publish(TranscriptionFailed(
                recording_id=recording_id,
                error_message=msg,
            ))
            return

        try:
            from teams_transcriber.audio.splitter import (
                probe_audio, split_channels_to_wav, to_mono_wav,
            )

            audio_path = Path(rec.audio_path)
            channels, _ = probe_audio(audio_path)

            if self._model is None:
                self._model = self._model_factory(
                    self._settings.transcription_model,
                    compute_type=self._settings.transcription_compute_type,
                )

            temp_wavs: list[Path] = []
            try:
                if channels == 2:
                    # Native dual-channel recording: mic = ME, system = OTHERS.
                    mic_wav = audio_path.with_suffix(".mic.wav")
                    loop_wav = audio_path.with_suffix(".loop.wav")
                    temp_wavs = [mic_wav, loop_wav]
                    split_channels_to_wav(audio_path, ch0_out=mic_wav, ch1_out=loop_wav)
                    me_segments = self._run_whisper(mic_wav, recording_id, Channel.ME)
                    others_segments = self._run_whisper(loop_wav, recording_id, Channel.OTHERS)
                    all_segments = sorted(
                        me_segments + others_segments, key=lambda s: s.start_ms,
                    )
                else:
                    # Imported / single-channel / multi-channel-not-dual audio:
                    # downmix to one mono WAV. Tag everything as ME — we can't
                    # know L/R mapping on arbitrary external files.
                    mono_wav = audio_path.with_suffix(".mono.wav")
                    temp_wavs = [mono_wav]
                    to_mono_wav(audio_path, mono_wav)
                    all_segments = self._run_whisper(mono_wav, recording_id, Channel.ME)

                if all_segments:
                    TranscriptRepo(self._db).append_many(all_segments)

                rec_repo.update_status(recording_id, RecordingStatus.SUMMARIZING)
                self._bus.publish(TranscriptionComplete(
                    recording_id=recording_id,
                    segment_count=len(all_segments) + len(existing),
                ))
            finally:
                for p in temp_wavs:
                    try:
                        if p.exists():
                            p.unlink()
                    except OSError:
                        logger.warning("could not delete temp wav %s", p)
        except Exception as exc:
            logger.exception("transcription failed for recording %d", recording_id)
            rec_repo.update_status(
                recording_id,
                RecordingStatus.TRANSCRIPTION_FAILED,
                error_message=str(exc),
            )
            self._bus.publish(TranscriptionFailed(
                recording_id=recording_id,
                error_message=str(exc),
            ))

    def _run_whisper(
        self, wav_path: Path, recording_id: int, channel: Channel,
    ) -> list[TranscriptSegment]:
        """Transcribe one mono WAV and return segments tagged with channel."""
        assert self._model is not None
        segments_iter, _info = self._model.transcribe(
            str(wav_path),
            language=self._settings.transcription_language,
            vad_filter=True,
        )
        result: list[TranscriptSegment] = []
        for seg in segments_iter:
            result.append(TranscriptSegment(
                id=None,
                recording_id=recording_id,
                start_ms=int(seg.start * 1000),
                end_ms=int(seg.end * 1000),
                channel=channel,
                text=seg.text.strip(),
            ))
        return result
