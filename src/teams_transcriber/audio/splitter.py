"""Split a 2-channel Opus file into per-channel mono WAVs for Whisper.

Whisper accepts WAV/mono/16 kHz natively and avoids re-encoding overhead.
Implemented via PyAV: decode → split planes → write two single-channel WAV files.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import av
import numpy as np

logger = logging.getLogger(__name__)


def split_channels_to_wav(
    opus_path: Path,
    *,
    ch0_out: Path,
    ch1_out: Path,
    output_rate: int = 16_000,
) -> None:
    """Decode `opus_path` (expected 2-channel) and write two mono 16-bit WAV files."""
    in_container = av.open(str(opus_path))
    try:
        in_stream = in_container.streams.audio[0]
        if in_stream.codec_context.channels != 2:
            raise ValueError(
                f"expected 2-channel input, got {in_stream.codec_context.channels}-channel"
            )

        ch0_container = av.open(str(ch0_out), mode="w", format="wav")
        ch1_container = av.open(str(ch1_out), mode="w", format="wav")
        try:
            ch0_stream = ch0_container.add_stream("pcm_s16le", rate=output_rate)
            ch0_stream.layout = "mono"
            ch1_stream = ch1_container.add_stream("pcm_s16le", rate=output_rate)
            ch1_stream.layout = "mono"

            resampler0 = av.AudioResampler(format="s16", layout="mono", rate=output_rate)
            resampler1 = av.AudioResampler(format="s16", layout="mono", rate=output_rate)

            for frame in in_container.decode(audio=0):
                arr = frame.to_ndarray()
                # PyAV's to_ndarray() shape depends on format:
                # - planar (fltp, etc.): (channels, samples)
                # - packed: (1, channels*samples) interleaved OR (channels*samples,)
                if arr.ndim == 1:
                    left = arr[0::2]
                    right = arr[1::2]
                elif arr.shape[0] == 2:
                    left = arr[0]
                    right = arr[1]
                elif arr.shape[0] == 1 and arr.shape[1] % 2 == 0:
                    flat = arr[0]
                    left = flat[0::2]
                    right = flat[1::2]
                else:
                    raise RuntimeError(f"unexpected frame shape {arr.shape}")

                ch0_frame = _make_mono_frame(left, frame.sample_rate, frame.format.name)
                ch1_frame = _make_mono_frame(right, frame.sample_rate, frame.format.name)

                for r in resampler0.resample(ch0_frame):
                    for p in ch0_stream.encode(r):
                        ch0_container.mux(p)
                for r in resampler1.resample(ch1_frame):
                    for p in ch1_stream.encode(r):
                        ch1_container.mux(p)

            # Flush resamplers + encoders.
            for r in resampler0.resample(None):
                for p in ch0_stream.encode(r):
                    ch0_container.mux(p)
            for p in ch0_stream.encode(None):
                ch0_container.mux(p)
            for r in resampler1.resample(None):
                for p in ch1_stream.encode(r):
                    ch1_container.mux(p)
            for p in ch1_stream.encode(None):
                ch1_container.mux(p)
        finally:
            ch0_container.close()
            ch1_container.close()
    finally:
        in_container.close()


def _make_mono_frame(samples: np.ndarray, sample_rate: int, fmt_name: str) -> Any:
    """Build a PyAV AudioFrame from a 1-D sample array, matching the source format."""
    samples = np.ascontiguousarray(samples)
    if samples.ndim == 1:
        samples = samples.reshape(1, -1)
    frame = av.AudioFrame.from_ndarray(samples, format=fmt_name, layout="mono")
    frame.sample_rate = sample_rate
    return frame


def probe_audio(src_path: Path) -> tuple[int, int]:
    """Return (channel_count, duration_ms) for any audio file PyAV can read.

    Raises ValueError if the file has no audio stream.
    """
    container = av.open(str(src_path))
    try:
        if not container.streams.audio:
            raise ValueError(f"no audio stream in {src_path}")
        s = container.streams.audio[0]
        channels = int(s.codec_context.channels)
        duration_ms = 0
        if s.duration is not None and s.time_base is not None:
            duration_ms = int(float(s.duration * s.time_base) * 1000)
        if duration_ms <= 0 and container.duration is not None:
            duration_ms = int(container.duration / 1000)   # av container duration is microseconds
        return channels, max(0, duration_ms)
    finally:
        container.close()


def to_mono_wav(src_path: Path, dst_wav: Path, *, output_rate: int = 16_000) -> None:
    """Decode any audio file and write a mono 16-bit WAV at `output_rate`.

    Multi-channel sources are downmixed by PyAV's resampler (output layout=mono),
    which is the right behavior for arbitrary external audio where L/R channel
    semantics aren't known. Native 2-channel recordings keep their own per-channel
    path via `split_channels_to_wav` (mic vs system audio).
    """
    in_container = av.open(str(src_path))
    try:
        if not in_container.streams.audio:
            raise ValueError(f"no audio stream in {src_path}")
        out_container = av.open(str(dst_wav), mode="w", format="wav")
        try:
            out_stream = out_container.add_stream("pcm_s16le", rate=output_rate)
            out_stream.layout = "mono"
            resampler = av.AudioResampler(format="s16", layout="mono", rate=output_rate)
            for frame in in_container.decode(audio=0):
                for r in resampler.resample(frame):
                    for p in out_stream.encode(r):
                        out_container.mux(p)
            # Flush resampler + encoder.
            for r in resampler.resample(None):
                for p in out_stream.encode(r):
                    out_container.mux(p)
            for p in out_stream.encode(None):
                out_container.mux(p)
        finally:
            out_container.close()
    finally:
        in_container.close()
