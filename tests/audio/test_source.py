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


def test_from_settings_id_match() -> None:
    """When saved id matches a real device, use it."""
    from teams_transcriber.audio.source import RealAudioSource

    class _Dev:
        def __init__(self, id_, name): self.id = id_; self.name = name

    fake_mics = [_Dev("{mic-a}", "Mic A"), _Dev("{mic-b}", "Mic B")]
    fake_speakers = [_Dev("{spk-a}", "Spk A"), _Dev("{spk-b}", "Spk B")]

    def get_microphone(id_, include_loopback=False):
        for d in fake_mics + fake_speakers:
            if d.id == id_:
                return d
        return None

    class _Settings:
        audio_mic_device = {"id": "{mic-b}", "name": "Mic B"}
        audio_loopback_device = {"id": "{spk-a}", "name": "Spk A"}

    result = RealAudioSource._resolve_devices(
        _Settings(),
        all_mics=fake_mics,
        all_speakers=fake_speakers,
        get_microphone=get_microphone,
        default_mic=fake_mics[0],
        default_speaker=fake_speakers[1],
    )
    assert result.mic.id == "{mic-b}"
    assert result.loopback.id == "{spk-a}"
    assert result.fallbacks == []


def test_from_settings_name_fallback_when_id_missing() -> None:
    """When saved id doesn't exist but name matches, use the name match (no fallback toast — same device renamed/reseated)."""
    from teams_transcriber.audio.source import RealAudioSource

    class _Dev:
        def __init__(self, id_, name): self.id = id_; self.name = name

    fake_mics = [_Dev("{mic-new}", "Sony Headset")]
    fake_speakers = [_Dev("{spk-x}", "Speakers X")]

    def get_microphone(id_, include_loopback=False):
        for d in fake_mics + fake_speakers:
            if d.id == id_:
                return d
        return None

    class _Settings:
        audio_mic_device = {"id": "{mic-OLD}", "name": "Sony Headset"}
        audio_loopback_device = None

    result = RealAudioSource._resolve_devices(
        _Settings(),
        all_mics=fake_mics,
        all_speakers=fake_speakers,
        get_microphone=get_microphone,
        default_mic=fake_mics[0],
        default_speaker=fake_speakers[0],
    )
    assert result.mic.id == "{mic-new}"
    assert result.fallbacks == []


def test_from_settings_full_default_fallback() -> None:
    """When neither id nor name match, fall back to default and report it."""
    from teams_transcriber.audio.source import RealAudioSource

    class _Dev:
        def __init__(self, id_, name): self.id = id_; self.name = name

    fake_mics = [_Dev("{mic-current}", "Current Mic")]
    fake_speakers = [_Dev("{spk-current}", "Current Speakers")]

    def get_microphone(id_, include_loopback=False):
        for d in fake_mics + fake_speakers:
            if d.id == id_:
                return d
        return None

    class _Settings:
        audio_mic_device = {"id": "{mic-gone}", "name": "Vanished Mic"}
        audio_loopback_device = {"id": "{spk-gone}", "name": "Vanished Speakers"}

    result = RealAudioSource._resolve_devices(
        _Settings(),
        all_mics=fake_mics,
        all_speakers=fake_speakers,
        get_microphone=get_microphone,
        default_mic=fake_mics[0],
        default_speaker=fake_speakers[0],
    )
    assert result.mic.id == "{mic-current}"
    assert result.loopback.id == "{spk-current}"
    assert set(result.fallbacks) == {("microphone", "Vanished Mic"),
                                      ("system audio", "Vanished Speakers")}


def test_from_settings_no_devices_at_all_raises() -> None:
    """When no defaults exist either, raise NoAudioDevicesError."""
    from teams_transcriber.audio.source import NoAudioDevicesError, RealAudioSource

    class _Settings:
        audio_mic_device = None
        audio_loopback_device = None

    import pytest
    with pytest.raises(NoAudioDevicesError):
        RealAudioSource._resolve_devices(
            _Settings(),
            all_mics=[],
            all_speakers=[],
            get_microphone=lambda *_a, **_kw: None,
            default_mic=None,
            default_speaker=None,
        )


def test_from_settings_calls_all_microphones_with_correct_kwarg(monkeypatch) -> None:
    """Regression — soundcard.all_microphones takes include_loopback, not
    exclude_monitors. Calling with a non-existent kwarg raises TypeError at
    runtime; this test guards against re-introducing it."""
    import sys
    from types import SimpleNamespace

    captured_kwargs: list[dict] = []

    # Strict signature — only `include_loopback` is accepted. Any other kwarg
    # (e.g., exclude_monitors) triggers TypeError, which from_settings would
    # surface as a build/test failure.
    def fake_all_microphones(*, include_loopback=False):
        captured_kwargs.append({"include_loopback": include_loopback})
        return []  # empty — exercise the no-mic raise path

    fake_sc = SimpleNamespace(
        all_microphones=fake_all_microphones,
        all_speakers=lambda: [],
        default_microphone=lambda: None,
        default_speaker=lambda: None,
        get_microphone=lambda *_a, **_kw: None,
    )
    monkeypatch.setitem(sys.modules, "soundcard", fake_sc)

    from teams_transcriber.audio.source import NoAudioDevicesError

    class _Settings:
        audio_mic_device = None
        audio_loopback_device = None

    # Expected to raise NoAudioDevicesError because we return empty lists —
    # which is fine; we just need to confirm all_microphones was called with
    # the right kwarg before the raise.
    import pytest
    with pytest.raises(NoAudioDevicesError):
        RealAudioSource.from_settings(_Settings())

    assert captured_kwargs == [{"include_loopback": False}]
