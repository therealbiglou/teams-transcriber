from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from teams_transcriber.audio.opus_writer import SAMPLE_RATE, OpusWriter


def _make_test_pcm(seconds: float, freq_hz: float, channels: int = 2) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.linspace(0.0, seconds, n, endpoint=False, dtype=np.float32)
    mono = (0.25 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
    if channels == 1:
        return mono.reshape(-1, 1)
    # Different sine on the right channel so we can tell channels apart later if needed.
    right = (0.25 * np.sin(2 * np.pi * (freq_hz * 1.5) * t)).astype(np.float32)
    return np.stack([mono, right], axis=1)


def test_write_then_close_produces_readable_opus(tmp_path: Path) -> None:
    path = tmp_path / "out.opus"
    writer = OpusWriter(path, channels=2, bitrate_kbps=24)
    pcm = _make_test_pcm(seconds=1.0, freq_hz=440.0, channels=2)
    writer.write_chunk(pcm)
    writer.close()

    assert path.exists()
    assert path.stat().st_size > 0

    # Read back via PyAV to confirm the file is valid Opus and has the right params.
    import av  # type: ignore[import-not-found]
    container = av.open(str(path))
    try:
        assert len(container.streams.audio) == 1
        stream = container.streams.audio[0]
        assert stream.codec_context.name == "opus"
        assert stream.codec_context.channels == 2
        # Decode all frames and confirm we got at least ~1 second of audio.
        total_samples = 0
        for frame in container.decode(audio=0):
            total_samples += frame.samples
        assert total_samples >= SAMPLE_RATE * 0.9  # tolerate small framing loss
    finally:
        container.close()


def test_multiple_chunks_concatenate(tmp_path: Path) -> None:
    path = tmp_path / "multi.opus"
    writer = OpusWriter(path, channels=2, bitrate_kbps=24)
    for _ in range(5):
        writer.write_chunk(_make_test_pcm(seconds=0.4, freq_hz=440.0, channels=2))
    writer.close()

    import av  # type: ignore[import-not-found]
    container = av.open(str(path))
    try:
        total = sum(f.samples for f in container.decode(audio=0))
        assert total >= int(SAMPLE_RATE * 2.0 * 0.9)  # 5 * 0.4s = 2s, allow 10% loss
    finally:
        container.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "ok.opus"
    writer = OpusWriter(path, channels=2, bitrate_kbps=24)
    writer.write_chunk(_make_test_pcm(seconds=0.2, freq_hz=440.0))
    writer.close()
    writer.close()  # must not raise


def test_write_after_close_raises(tmp_path: Path) -> None:
    path = tmp_path / "ok.opus"
    writer = OpusWriter(path, channels=2, bitrate_kbps=24)
    writer.close()
    with pytest.raises(RuntimeError, match="closed"):
        writer.write_chunk(_make_test_pcm(seconds=0.1, freq_hz=440.0))


def test_rejects_wrong_channel_count(tmp_path: Path) -> None:
    path = tmp_path / "ok.opus"
    writer = OpusWriter(path, channels=2, bitrate_kbps=24)
    mono = _make_test_pcm(seconds=0.1, freq_hz=440.0, channels=1)
    with pytest.raises(ValueError, match="channels"):
        writer.write_chunk(mono)
    writer.close()
