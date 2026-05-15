from __future__ import annotations

import sys

import pytest

from teams_transcriber.audio.devices import (
    AudioDevice,
    default_loopback,
    default_microphone,
    list_microphones,
    list_speakers,
)


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows audio devices only")
def test_default_microphone_returns_something() -> None:
    mic = default_microphone()
    assert mic is None or isinstance(mic, AudioDevice)
    if mic:
        assert mic.name


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows audio devices only")
def test_default_loopback_returns_something() -> None:
    lb = default_loopback()
    assert lb is None or isinstance(lb, AudioDevice)
    if lb:
        assert lb.is_loopback is True


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows audio devices only")
def test_list_returns_iterable() -> None:
    assert isinstance(list_microphones(), list)
    assert isinstance(list_speakers(), list)


def test_audio_device_dataclass_shape() -> None:
    d = AudioDevice(id="abc", name="My Mic", is_loopback=False)
    assert d.id == "abc"
    assert d.is_loopback is False
