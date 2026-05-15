"""Audio source abstraction so tests can drive the Recorder without real devices."""

from __future__ import annotations

import threading
import time
from typing import Protocol

import numpy as np


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
