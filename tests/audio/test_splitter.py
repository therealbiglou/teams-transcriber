from __future__ import annotations

from pathlib import Path

import numpy as np

from teams_transcriber.audio.opus_writer import SAMPLE_RATE, OpusWriter
from teams_transcriber.audio.splitter import split_channels_to_wav


def _two_channel_pcm(seconds: float) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.linspace(0, seconds, n, endpoint=False, dtype=np.float32)
    left = (0.25 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    right = (0.25 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
    return np.stack([left, right], axis=1)


def test_split_writes_two_mono_wavs(tmp_path: Path) -> None:
    opus_path = tmp_path / "stereo.opus"
    writer = OpusWriter(opus_path, channels=2, bitrate_kbps=24)
    writer.write_chunk(_two_channel_pcm(1.0))
    writer.close()

    out_ch0 = tmp_path / "ch0.wav"
    out_ch1 = tmp_path / "ch1.wav"
    split_channels_to_wav(opus_path, ch0_out=out_ch0, ch1_out=out_ch1)

    assert out_ch0.exists() and out_ch0.stat().st_size > 0
    assert out_ch1.exists() and out_ch1.stat().st_size > 0

    # Read back via PyAV and confirm each is mono and ~1 second.
    import av  # type: ignore[import-not-found]
    for path in (out_ch0, out_ch1):
        c = av.open(str(path))
        try:
            stream = c.streams.audio[0]
            assert stream.codec_context.channels == 1
            total = sum(f.samples for f in c.decode(audio=0))
            assert total >= int(SAMPLE_RATE * 0.9)
        finally:
            c.close()
