"""Light wrapper over `soundcard` for default mic + speaker loopback enumeration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class AudioDevice:
    """An audio input device. Loopbacks are inputs that capture system output."""

    id: str
    name: str
    is_loopback: bool


def _safe_import_soundcard() -> object | None:
    try:
        import soundcard
    except (OSError, ImportError):
        # On non-Windows or where COM is unavailable, soundcard's import raises.
        return None
    return soundcard  # type: ignore[no-any-return]


def default_microphone() -> AudioDevice | None:
    sc = _safe_import_soundcard()
    if sc is None:
        return None
    try:
        m = sc.default_microphone()  # type: ignore[attr-defined]
    except RuntimeError:
        return None
    return AudioDevice(id=m.id, name=m.name, is_loopback=False)


def default_loopback() -> AudioDevice | None:
    """Return a loopback device for the default speaker (captures system audio)."""
    sc = _safe_import_soundcard()
    if sc is None:
        return None
    try:
        speaker = sc.default_speaker()  # type: ignore[attr-defined]
        # soundcard exposes a loopback-input for each speaker; ask for it explicitly.
        loop = sc.get_microphone(speaker.id, include_loopback=True)  # type: ignore[attr-defined]
    except RuntimeError:
        return None
    return AudioDevice(id=loop.id, name=loop.name, is_loopback=True)


def list_microphones() -> list[AudioDevice]:
    sc = _safe_import_soundcard()
    if sc is None:
        return []
    return [
        AudioDevice(id=m.id, name=m.name, is_loopback=False)
        for m in sc.all_microphones(include_loopback=False)  # type: ignore[attr-defined]
    ]


def list_speakers() -> list[AudioDevice]:
    sc = _safe_import_soundcard()
    if sc is None:
        return []
    # Speakers exposed as loopback inputs.
    return [
        AudioDevice(id=m.id, name=m.name, is_loopback=True)
        for m in sc.all_microphones(include_loopback=True)  # type: ignore[attr-defined]
        if getattr(m, "isloopback", False)
    ]
