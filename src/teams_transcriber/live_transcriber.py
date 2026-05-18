"""Live transcription: alternating single-instance Whisper across mic/loopback."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from teams_transcriber.config import Settings
from teams_transcriber.events import (
    EventBus,
    LiveSegmentAvailable,
    LiveTranscriptionDegraded,
)
from teams_transcriber.storage import (
    Channel,
    Database,
    TranscriptRepo,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

ModelFactory = Callable[..., Any]


def _default_model_factory(model_name: str, *, compute_type: str) -> Any:
    from faster_whisper import WhisperModel
    return WhisperModel(model_name, compute_type=compute_type)


class LiveTranscriber:
    """Single-instance Whisper, strictly alternating between two channels.

    Lifecycle:
        lt = LiveTranscriber(bus=..., db=..., settings=...)
        lt.start(recording_id)
        lt.feed(Channel.ME, mono_pcm_float32_array)
        ...
        lt.flush_and_stop()
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        db: Database,
        settings: Settings,
        model_factory: ModelFactory = _default_model_factory,
        flush_interval_ms: int | None = None,
        max_wait_ms: int | None = None,
    ) -> None:
        self._bus = bus
        self._db = db
        self._settings = settings
        self._model_factory = model_factory
        self._flush_interval_ms = (
            flush_interval_ms if flush_interval_ms is not None
            else settings.transcription_live_flush_interval_ms
        )
        self._max_wait_ms = (
            max_wait_ms if max_wait_ms is not None
            else settings.transcription_live_max_wait_ms
        )
        self._recording_id: int | None = None
        self._model: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Single condition variable guards all shared mutable state:
        # _buffers, _next_channel, _last_pass_ms.
        # feed() appends under the lock and notifies so the worker wakes up
        # immediately when a threshold may have been crossed.
        self._cond = threading.Condition(threading.Lock())
        self._buffers: dict[Channel, list[np.ndarray]] = {
            Channel.ME: [],
            Channel.OTHERS: [],
        }
        self._last_pass_ms: dict[Channel, float] = {
            Channel.ME: 0.0,
            Channel.OTHERS: 0.0,
        }
        self._next_channel: Channel = Channel.ME
        # Incremented each time a pass completes; feed() can wait on this to
        # let the worker drain the current next-in-line channel before the
        # caller queues data for the other channel.
        self._pass_count: int = 0

    # --- public API --------------------------------------------------------

    def start(self, recording_id: int) -> None:
        if self._thread is not None:
            raise RuntimeError("LiveTranscriber already started")
        self._recording_id = recording_id
        self._stop.clear()
        now_ms = time.monotonic() * 1000.0
        with self._cond:
            self._last_pass_ms = {Channel.ME: now_ms, Channel.OTHERS: now_ms}
            self._pass_count = 0
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="live-transcriber",
        )
        self._thread.start()

    def feed(self, channel: Channel, pcm_mono: np.ndarray) -> None:
        """Append mono float32 PCM @ 16 kHz to the channel's buffer. Non-blocking."""
        if self._thread is None or self._stop.is_set():
            return
        if pcm_mono.dtype != np.float32:
            pcm_mono = pcm_mono.astype(np.float32, copy=False)
        with self._cond:
            self._buffers[channel].append(pcm_mono)
            self._cond.notify_all()

    def flush_and_stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self._thread.join(timeout=30.0)
        if self._thread.is_alive():
            logger.warning("LiveTranscriber worker did not exit within 30s")
        self._thread = None

    # --- worker ------------------------------------------------------------

    def _run(self) -> None:
        try:
            while True:
                with self._cond:
                    channel = self._next_channel
                    should = self._should_process_locked(channel)
                if should:
                    audio = self._consume_buffer(channel)
                    if audio is not None and len(audio) > 0:
                        self._process_pass(channel, audio)
                    with self._cond:
                        self._last_pass_ms[channel] = time.monotonic() * 1000.0
                        self._next_channel = (
                            Channel.OTHERS if channel == Channel.ME else Channel.ME
                        )
                        self._pass_count += 1
                        self._cond.notify_all()
                elif self._stop.is_set():
                    # Final drain: process current channel then the other.
                    audio = self._consume_buffer(channel)
                    if audio is not None and len(audio) > 0:
                        self._process_pass(channel, audio)
                    other = (
                        Channel.OTHERS if channel == Channel.ME else Channel.ME
                    )
                    audio = self._consume_buffer(other)
                    if audio is not None and len(audio) > 0:
                        self._process_pass(other, audio)
                    break
                else:
                    # Wait for new audio or for max_wait_ms / 4 to elapse.
                    wait_s = max(0.005, self._max_wait_ms / 4 / 1000.0)
                    with self._cond:
                        self._cond.wait(timeout=wait_s)
        except Exception:
            logger.exception("LiveTranscriber worker crashed")
            if self._recording_id is not None:
                self._bus.publish(LiveTranscriptionDegraded(
                    recording_id=self._recording_id,
                    reason="worker thread crashed",
                ))

    def _threshold_met(self, channel: Channel) -> bool:
        """Check if buffered samples cross flush_interval_ms. MUST be called under self._cond."""
        buffered_samples = sum(arr.shape[0] for arr in self._buffers[channel])
        buffered_ms = (buffered_samples / SAMPLE_RATE) * 1000.0
        return buffered_ms >= self._flush_interval_ms

    def _should_process_locked(self, channel: Channel) -> bool:
        """Check all trigger conditions. MUST be called under self._cond."""
        buffered_samples = sum(arr.shape[0] for arr in self._buffers[channel])
        buffered_ms = (buffered_samples / SAMPLE_RATE) * 1000.0
        now_ms = time.monotonic() * 1000.0
        elapsed_ms = now_ms - self._last_pass_ms[channel]
        if self._stop.is_set() and buffered_samples > 0:
            return True
        if buffered_ms >= self._flush_interval_ms:
            return True
        return elapsed_ms >= self._max_wait_ms and buffered_samples > 0

    def _consume_buffer(self, channel: Channel) -> np.ndarray | None:
        with self._cond:
            chunks = self._buffers[channel]
            self._buffers[channel] = []
        if not chunks:
            return None
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def _process_pass(self, channel: Channel, audio: np.ndarray) -> None:
        if self._model is None:
            try:
                self._model = self._model_factory(
                    self._settings.transcription_model,
                    compute_type=self._settings.transcription_compute_type,
                )
            except Exception:
                logger.exception("failed to load Whisper model for live transcription")
                if self._recording_id is not None:
                    self._bus.publish(LiveTranscriptionDegraded(
                        recording_id=self._recording_id,
                        reason="model load failed",
                    ))
                return
        try:
            segments_iter, _info = self._model.transcribe(
                audio,
                language=self._settings.transcription_language,
                vad_filter=True,
            )
        except Exception:
            logger.exception("model.transcribe raised in live mode")
            if self._recording_id is not None:
                self._bus.publish(LiveTranscriptionDegraded(
                    recording_id=self._recording_id,
                    reason="model.transcribe raised",
                ))
            return

        repo = TranscriptRepo(self._db)
        assert self._recording_id is not None
        for seg in segments_iter:
            try:
                text = seg.text.strip()
            except AttributeError:
                continue
            if not text:
                continue
            ts = TranscriptSegment(
                id=None,
                recording_id=self._recording_id,
                start_ms=int(seg.start * 1000),
                end_ms=int(seg.end * 1000),
                channel=channel,
                text=text,
            )
            try:
                repo.append(ts)
            except Exception:
                logger.exception("TranscriptRepo.append failed in live mode")
                self._bus.publish(LiveTranscriptionDegraded(
                    recording_id=self._recording_id,
                    reason="db append failed",
                ))
                return
            self._bus.publish(LiveSegmentAvailable(
                recording_id=self._recording_id, segment=ts,
            ))
