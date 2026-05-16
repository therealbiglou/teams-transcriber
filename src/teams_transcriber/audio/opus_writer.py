"""Streaming PCM to Ogg/Opus encoder.

Wraps PyAV. The capture thread calls `write_chunk(pcm)` with `(frames, channels)`
float32 arrays at 16 kHz; we resample-up to 48 kHz (Opus's preferred rate) inside
PyAV. `close()` flushes any pending encoder output and finalizes the container.

The writer is intended for use by a single producing thread. Concurrent writers
on the same instance are not supported (and not needed).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import av
import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE: int = 16_000  # Whisper's native rate; we don't resample later.
ENCODE_RATE: int = 48_000  # Opus's native rate; PyAV resamples internally.


class OpusWriter:
    def __init__(self, path: Path, *, channels: int = 2, bitrate_kbps: int = 24) -> None:
        self._path = path
        self._channels = channels
        self._closed = False

        path.parent.mkdir(parents=True, exist_ok=True)

        self._container = av.open(str(path), mode="w", format="ogg")
        self._stream = self._container.add_stream("libopus", rate=ENCODE_RATE)
        self._stream.layout = "stereo" if channels == 2 else "mono"
        # Per-channel bitrate x channels.
        self._stream.bit_rate = int(bitrate_kbps * 1000 * channels)

        # Lazy resampler reference (created on first write).
        self._resampler: Any = None
        self._samples_written = 0

    def write_chunk(self, pcm: np.ndarray) -> None:
        """Encode a (frames, channels) float32 PCM block at SAMPLE_RATE."""
        if self._closed:
            raise RuntimeError("OpusWriter is closed")
        if pcm.ndim != 2 or pcm.shape[1] != self._channels:
            raise ValueError(
                f"expected (frames, {self._channels}) array with matching channels, "
                f"got {pcm.shape}"
            )
        if pcm.dtype != np.float32:
            pcm = pcm.astype(np.float32, copy=False)

        layout = "stereo" if self._channels == 2 else "mono"
        frame = av.AudioFrame.from_ndarray(
            pcm.T.copy(order="C"),  # PyAV wants shape (channels, samples)
            format="fltp",  # float planar
            layout=layout,
        )
        frame.sample_rate = SAMPLE_RATE

        if self._resampler is None:
            self._resampler = av.AudioResampler(
                format="fltp",
                layout=layout,
                rate=ENCODE_RATE,
            )

        for resampled in self._resampler.resample(frame):
            for packet in self._stream.encode(resampled):
                self._container.mux(packet)
                self._samples_written += resampled.samples

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Flush any frames buffered in the resampler.
        if self._resampler is not None:
            try:
                for resampled in self._resampler.resample(None):
                    for packet in self._stream.encode(resampled):
                        self._container.mux(packet)
            except Exception:
                logger.exception("resampler flush failed")
        # Flush the encoder.
        try:
            for packet in self._stream.encode(None):
                self._container.mux(packet)
        except Exception:
            logger.exception("encoder flush failed")
        try:
            self._container.close()
        except Exception:
            logger.exception("container close failed")
