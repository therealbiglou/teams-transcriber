"""Captures audio from an AudioSource into a 2-channel Opus file, with DB state.

Phase 2 ships post-mode transcription, so the recorder finalizes the file and
sets the recording row to `transcribing`; the Transcriber picks it up from there.

This module abstracts the audio source so tests can use FakeAudioSource. The
default real implementation lives in audio/source_real.py (added in Phase 2.5
when we wire the actual `soundcard.Recorder`).
"""

from __future__ import annotations

import logging
import re
import threading
import warnings
from datetime import UTC, datetime
from pathlib import Path

from teams_transcriber.audio.opus_writer import SAMPLE_RATE, OpusWriter
from teams_transcriber.audio.source import AudioSource
from teams_transcriber.config import Settings
from teams_transcriber.events import (
    EventBus,
    RecordingFailed,
    RecordingFinalized,
    RecordingStarted,
)
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    Database,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
)

logger = logging.getLogger(__name__)

CHUNK_FRAMES: int = SAMPLE_RATE  # 1-second chunks


def _slug(text: str | None) -> str:
    if not text:
        return "manual"
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (cleaned or "manual")[:40]


class Recorder:
    def __init__(
        self,
        *,
        bus: EventBus,
        db: Database,
        paths: AppPaths,
        settings: Settings,
        audio_source: AudioSource,
    ) -> None:
        self._bus = bus
        self._db = db
        self._paths = paths
        self._settings = settings
        self._source = audio_source
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._recording_id: int | None = None
        self._audio_path: Path | None = None
        self._writer: OpusWriter | None = None
        self._started_at: datetime | None = None
        self._frames_written: int = 0

    def start(self, *, source_type: str, detected_title: str | None) -> int:
        if self._thread is not None:
            raise RuntimeError("Recorder already running; call stop() first")
        self._started_at = datetime.now(UTC)
        repo = RecordingRepo(self._db)

        slug = _slug(detected_title)
        self._paths.audio_dir.mkdir(parents=True, exist_ok=True)
        base = self._started_at.strftime("%Y-%m-%d_%H%M%S")
        candidate = self._paths.audio_dir / f"{base}_{slug}.opus"
        suffix = 1
        while candidate.exists():
            candidate = self._paths.audio_dir / f"{base}_{slug}-{suffix}.opus"
            suffix += 1
        self._audio_path = candidate
        self._frames_written = 0

        rec = repo.create(Recording(
            id=None,
            started_at=self._started_at.isoformat(),
            ended_at=None,
            source=RecordingSource(source_type),
            detected_title=detected_title,
            display_title=None,
            audio_path=str(self._audio_path),
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        ))
        assert rec.id is not None
        self._recording_id = rec.id

        self._writer = OpusWriter(
            self._audio_path,
            channels=2,
            bitrate_kbps=self._settings.audio_bitrate_kbps,
        )

        self._bus.publish(RecordingStarted(
            recording_id=rec.id, audio_path=str(self._audio_path),
        ))

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="recorder")
        self._thread.start()
        return rec.id

    def stop(self) -> None:
        """Stop recording, finalize the file, transition status to TRANSCRIBING."""
        self._end(cancel=False)

    def cancel(self) -> None:
        """Abort: delete file, remove DB row."""
        self._end(cancel=True)

    # --- internals ---------------------------------------------------------

    def _run(self) -> None:
        assert self._writer is not None
        try:
            with warnings.catch_warnings():
                # soundcard logs benign 'data discontinuity' warnings hundreds of times
                # per meeting; suppress them at the worker scope only.
                warnings.filterwarnings(
                    "ignore", module="soundcard.*", message=".*discontinuity.*",
                )
                while not self._stop.is_set():
                    chunk = self._source.read_chunk(CHUNK_FRAMES)
                    if chunk.shape[0] == 0:
                        break
                    self._writer.write_chunk(chunk)
                    self._frames_written += chunk.shape[0]
        except Exception as exc:
            logger.exception("recorder loop failed")
            repo = RecordingRepo(self._db)
            if self._recording_id is not None:
                repo.update_status(
                    self._recording_id, RecordingStatus.RECORDING_FAILED,
                    error_message=str(exc),
                )
                self._bus.publish(RecordingFailed(
                    recording_id=self._recording_id,
                    error_message=str(exc),
                ))

    def _end(self, *, cancel: bool) -> None:
        if self._thread is None:
            return
        self._stop.set()
        # Wake the source if it's blocking; FakeAudioSource doesn't block but real ones may.
        try:
            self._source.close()
        except Exception:
            logger.exception("source.close() raised")
        self._thread.join(timeout=5.0)
        self._thread = None

        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                logger.exception("writer.close() raised")
            self._writer = None

        repo = RecordingRepo(self._db)
        if self._recording_id is None:
            return

        if cancel:
            if self._audio_path is not None and self._audio_path.exists():
                try:
                    self._audio_path.unlink()
                except OSError:
                    logger.exception("could not delete %s", self._audio_path)
            repo.delete(self._recording_id)
        else:
            ended_at = datetime.now(UTC)
            assert self._started_at is not None
            # Duration is derived from samples written, not wall-clock; this matches the
            # actual audio in the file (e.g. a FakeAudioSource that pushes 1.5s of PCM
            # in 100 ms wall-clock should still report 1500 ms duration).
            duration_ms = int(self._frames_written * 1000 / SAMPLE_RATE)
            repo.finalize(
                self._recording_id,
                ended_at=ended_at.isoformat(),
                duration_ms=duration_ms,
            )
            repo.update_status(self._recording_id, RecordingStatus.TRANSCRIBING)
            self._bus.publish(RecordingFinalized(
                recording_id=self._recording_id, duration_ms=duration_ms,
            ))

        self._recording_id = None
        self._audio_path = None
        self._started_at = None
        self._frames_written = 0
