from __future__ import annotations

from typing import Any

import numpy as np

from teams_transcriber.audio.source import RealAudioSource

# --- Fake soundcard recorder ---------------------------------------------

class _FakeStream:
    """Mimics the `soundcard.Recorder` context manager + `record(n)` API."""

    def __init__(self, samples: np.ndarray, samplerate: int = 16_000) -> None:
        assert samples.ndim == 1
        self._samples = samples.astype(np.float32)
        self._samplerate = samplerate
        self._cursor = 0
        self._closed = False

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *_a: Any) -> None:
        self._closed = True

    def record(self, num_frames: int) -> np.ndarray:
        if self._closed:
            raise RuntimeError("stream closed")
        end = min(self._cursor + num_frames, len(self._samples))
        if self._cursor >= len(self._samples):
            return np.zeros(num_frames, dtype=np.float32)
        chunk = self._samples[self._cursor:end]
        self._cursor = end
        if len(chunk) < num_frames:
            chunk = np.concatenate([chunk, np.zeros(num_frames - len(chunk), dtype=np.float32)])
        return chunk


class _FakeDevice:
    def __init__(self, samples: np.ndarray) -> None:
        self._samples = samples

    def recorder(self, samplerate: int, channels: int = 1, blocksize: int | None = None) -> _FakeStream:
        _ = (channels, blocksize)  # unused in the fake
        return _FakeStream(self._samples, samplerate=samplerate)


def _sine(seconds: float, freq_hz: float, samplerate: int = 16_000) -> np.ndarray:
    n = int(seconds * samplerate)
    t = np.linspace(0, seconds, n, endpoint=False, dtype=np.float32)
    return (0.25 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


def test_real_source_reads_stacked_chunks() -> None:
    mic_dev = _FakeDevice(_sine(1.0, 440))
    loop_dev = _FakeDevice(_sine(1.0, 880))
    src = RealAudioSource(mic_device=mic_dev, loopback_device=loop_dev)  # type: ignore[arg-type]
    try:
        first = src.read_chunk(1024)
        assert first.shape == (1024, 2)
        assert first.dtype == np.float32
        assert first[:, 0].any() and first[:, 1].any()
    finally:
        src.close()


def test_real_source_close_signals_end() -> None:
    mic_dev = _FakeDevice(_sine(0.05, 440))
    loop_dev = _FakeDevice(_sine(0.05, 880))
    src = RealAudioSource(mic_device=mic_dev, loopback_device=loop_dev)  # type: ignore[arg-type]
    src.close()
    out = src.read_chunk(1024)
    assert out.shape[0] == 0


def test_real_source_handles_short_chunks() -> None:
    mic_dev = _FakeDevice(_sine(0.1, 440))
    loop_dev = _FakeDevice(_sine(0.1, 880))
    src = RealAudioSource(
        mic_device=mic_dev, loopback_device=loop_dev,  # type: ignore[arg-type]
        chunk_frames=256,
    )
    try:
        total = 0
        for _ in range(20):
            c = src.read_chunk(256)
            if c.shape[0] == 0:
                break
            assert c.shape[1] == 2
            total += c.shape[0]
        assert total >= 1600
    finally:
        src.close()
