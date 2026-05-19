"""Audio source abstraction so tests can drive the Recorder without real devices."""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger(__name__)

CAPTURE_BLOCK_FRAMES: int = 1024  # ~64 ms at 16 kHz; small enough for tight stop response
SAMPLE_RATE: int = 16_000


class NoAudioDevicesError(RuntimeError):
    """Raised when neither a saved nor default audio device is available."""


class AudioSource(Protocol):
    """Yields (frames, 2) float32 PCM at 16 kHz.

    `read_chunk(num_frames)` returns up to `num_frames` rows. Returning a 0-row
    array signals end-of-stream (recorder stops).
    """

    def read_chunk(self, num_frames: int) -> np.ndarray: ...
    def close(self) -> None: ...


class FakeAudioSource:
    """A scripted source that yields pre-supplied mic + loopback mono streams.

    Both arrays must be the same length. Each `read_chunk` call advances a cursor;
    `run_until_exhausted` waits (in a test thread) until everything has been read.
    """

    def __init__(self, mic_samples: np.ndarray, loopback_samples: np.ndarray) -> None:
        assert mic_samples.ndim == 1
        assert loopback_samples.shape == mic_samples.shape
        self._mic = mic_samples.astype(np.float32, copy=False)
        self._loop = loopback_samples.astype(np.float32, copy=False)
        self._cursor = 0
        self._lock = threading.Lock()
        self._exhausted = threading.Event()

    def read_chunk(self, num_frames: int) -> np.ndarray:
        with self._lock:
            start = self._cursor
            end = min(start + num_frames, len(self._mic))
            self._cursor = end
            if start >= end:
                self._exhausted.set()
                return np.empty((0, 2), dtype=np.float32)
            mic_slice = self._mic[start:end]
            loop_slice = self._loop[start:end]
        return np.stack([mic_slice, loop_slice], axis=1)

    def close(self) -> None:
        with self._lock:
            self._cursor = len(self._mic)
            self._exhausted.set()

    # --- test helpers ------------------------------------------------------

    def run_until_exhausted(self, timeout: float = 5.0) -> None:
        self._exhausted.wait(timeout=timeout)

    def run_until_samples(self, n: int, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._cursor >= n:
                    return
            time.sleep(0.01)


class RealAudioSource:
    """Captures mic + loopback as a (frames, 2) float32 stream at 16 kHz.

    Two `soundcard` streams run in a single worker thread, each `.record(N)`-ed in
    lockstep, then column-stacked into (frames, 2). Chunks accumulate in a bounded
    queue; the Recorder pulls them via `read_chunk()`. `close()` shuts down cleanly.

    Tests inject fake devices via `mic_device` / `loopback_device`. Production
    constructs the source via `from_default_devices()`.
    """

    def __init__(
        self,
        *,
        mic_device: Any,
        loopback_device: Any,
        chunk_frames: int = CAPTURE_BLOCK_FRAMES,
        queue_size: int = 64,
    ) -> None:
        self._mic_device = mic_device
        self._loopback_device = loopback_device
        self._chunk_frames = chunk_frames
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="audio-capture")
        self._closed = False
        self._thread.start()

    @classmethod
    def from_default_devices(cls) -> RealAudioSource:
        """Construct using soundcard's default mic and a loopback of the default speaker."""
        import soundcard

        mic = soundcard.default_microphone()
        speaker = soundcard.default_speaker()
        loopback = soundcard.get_microphone(speaker.id, include_loopback=True)
        return cls(mic_device=mic, loopback_device=loopback)

    def read_chunk(self, num_frames: int) -> np.ndarray:
        """Pull the next chunk; 0-row array signals end-of-stream."""
        del num_frames  # We deliver fixed-size chunks; caller-requested size is advisory.
        item = self._queue.get()
        if item is None:
            return np.empty((0, 2), dtype=np.float32)
        return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        # Unblock the worker if it's wedged on a full queue.
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        self._thread.join(timeout=2.0)
        # Drain anything the worker put in between (its final sentinel + late chunks)
        # so the next read_chunk() sees end-of-stream.
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)

    # --- internals ---------------------------------------------------------

    def _run(self) -> None:
        try:
            with self._mic_device.recorder(samplerate=SAMPLE_RATE, channels=1) as mic_stream, \
                 self._loopback_device.recorder(samplerate=SAMPLE_RATE, channels=1) as loop_stream:
                while not self._stop.is_set():
                    try:
                        mic = mic_stream.record(self._chunk_frames)
                        loop = loop_stream.record(self._chunk_frames)
                    except Exception:
                        logger.exception("audio stream read failed")
                        break

                    mic = np.asarray(mic, dtype=np.float32).reshape(-1)
                    loop = np.asarray(loop, dtype=np.float32).reshape(-1)

                    n = min(len(mic), len(loop))
                    if n == 0:
                        continue
                    stacked = np.stack([mic[:n], loop[:n]], axis=1)

                    try:
                        self._queue.put(stacked, timeout=1.0)
                    except queue.Full:
                        logger.warning("audio queue full; dropping a chunk")
        finally:
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(None)
