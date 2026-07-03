# Phase 6 — Detection & Audio Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship four small subsystems — explicit microphone + system-audio selection in Settings (persisted by ID with name fallback), WASAPI session-based Teams meeting detection (hybrid with title patterns), a `live_enabled = False` toggle that makes Phase 5 live transcription opt-in, and clear error UX when no audio devices are available — all on branch `feature/phase-6-detection-audio-selection`.

**Architecture:** Settings store now-existing `audio.mic_device` / `audio.loopback_device` keys as dicts `{id, name}` (with `None` meaning "Windows default"). A new `RealAudioSource.from_settings()` does the lookup-ladder. A new `wasapi_sessions.py` exposes `teams_active_capture_pids()` (via `pycaw` COM bindings) for hybrid detection. `MeetingWatcher` consults that probe first, falls back to title matching. `Pipeline._start_recorder` gates `LiveTranscriber` on a new `transcription.live_enabled` setting and catches `NoAudioDevicesError` cleanly.

**Tech Stack:** Python 3.11, `uv`, PySide6 (Qt 6), `soundcard` (WASAPI capture), **`pycaw`** (new dependency for WASAPI session enumeration), `keyboard`, faster-whisper, SQLite, pytest, pytest-qt.

---

## File Structure

**New files:**

- `src/teams_transcriber/audio/wasapi_sessions.py` — `teams_active_capture_pids()` function that returns PIDs of Teams processes holding active mic capture sessions, via `pycaw`. All COM/import errors degrade to empty-set return.
- `tests/audio/test_wasapi_sessions.py` — unit tests with `pycaw` fully mocked.
- `tests/test_phase6_pipeline.py` — Phase-6-specific pipeline integration tests (live disabled, no-devices error, etc.).
- `docs/superpowers/checklists/2026-05-19-phase-6-verification.md` — manual verification checklist.

**Modified files:**

- `pyproject.toml` — add `pycaw>=20231007` to `[project.dependencies]`.
- `src/teams_transcriber/config.py` — add `transcription.live_enabled` default, change `audio.mic_device` / `audio.loopback_device` storage to dict, add typed properties.
- `src/teams_transcriber/events.py` — add `RecordingDeviceFallback` event.
- `src/teams_transcriber/audio/source.py` — add `NoAudioDevicesError`, add `RealAudioSource.from_settings(settings)` factory with lookup ladder, keep `from_default_devices()` as a thin wrapper.
- `src/teams_transcriber/meeting_watcher.py` — add `audio_session_probe` constructor param, hybrid `_find_meeting_window`.
- `src/teams_transcriber/pipeline.py` — gate `LiveTranscriber` on `transcription_live_enabled`, catch `NoAudioDevicesError` and publish `RecordingFailed`, publish `RecordingDeviceFallback` events from the source factory's fallback path.
- `src/teams_transcriber/ui/qt_bridge.py` — add `recording_device_fallback` signal.
- `src/teams_transcriber/ui/settings_dialog.py` — add Audio tab (mic + loopback dropdowns + move retention + bitrate), add live_enabled checkbox to Transcription tab.
- `src/teams_transcriber/ui/workspace_window.py` — placeholder transcript pane when `live=True` but `transcription_live_enabled=False`, reload on `SummaryReady`.
- `src/teams_transcriber/ui/app.py` — extend `_on_recording_failed` to add "Open Settings" action for no-devices case, subscribe to `RecordingDeviceFallback` toast.

---

## Note on running tests

`uv` is not on PATH on the build machine. Subagents must use the full path:

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest ...
```

---

## Task 1: Add `live_enabled` setting

**Files:**
- Modify: `src/teams_transcriber/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1.1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_live_enabled_default_is_false() -> None:
    """Phase 6 makes live transcription opt-in — default off."""
    from teams_transcriber.config import load_settings
    from teams_transcriber.paths import AppPaths

    import tempfile
    with tempfile.TemporaryDirectory() as tmp_path:
        paths = AppPaths(root=tmp_path)
        paths.ensure_dirs()
        s = load_settings(paths)
        assert s.transcription_live_enabled is False
```

- [ ] **Step 1.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/test_config.py -k live_enabled -v
```

Expected: AttributeError.

- [ ] **Step 1.3: Implement**

In `src/teams_transcriber/config.py`, update `DEFAULT_SETTINGS["transcription"]`:

```python
    "transcription": {
        "model": "large-v3-turbo",
        "compute_type": "int8_float16",
        "language": "en",
        "live_enabled": False,
        "live_dual_channel": False,
        "live_flush_interval_ms": 10_000,
        "live_max_wait_ms": 15_000,
    },
```

Add a property on the `Settings` class (near the existing transcription properties):

```python
    @property
    def transcription_live_enabled(self) -> bool:
        return bool(self._raw["transcription"].get("live_enabled", False))
```

- [ ] **Step 1.4: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/test_config.py -v
```

- [ ] **Step 1.5: Commit**

```powershell
git add src/teams_transcriber/config.py tests/test_config.py
git commit -m "feat(config): add transcription.live_enabled default=False"
```

---

## Task 2: Audio device dict settings

**Files:**
- Modify: `src/teams_transcriber/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 2.1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_audio_device_dict_round_trip() -> None:
    """audio_mic_device and audio_loopback_device round-trip through settings.json."""
    import tempfile
    from teams_transcriber.config import load_settings, save_settings
    from teams_transcriber.paths import AppPaths

    with tempfile.TemporaryDirectory() as tmp_path:
        paths = AppPaths(root=tmp_path)
        paths.ensure_dirs()
        s = load_settings(paths)
        assert s.audio_mic_device is None
        assert s.audio_loopback_device is None

        s._raw["audio"]["mic_device"] = {"id": "{mic-id-1}", "name": "Realtek Mic"}
        s._raw["audio"]["loopback_device"] = {"id": "{spk-id-1}", "name": "Realtek Speakers"}
        save_settings(paths, s)

        s2 = load_settings(paths)
        assert s2.audio_mic_device == {"id": "{mic-id-1}", "name": "Realtek Mic"}
        assert s2.audio_loopback_device == {"id": "{spk-id-1}", "name": "Realtek Speakers"}


def test_audio_device_legacy_string_loads_as_none() -> None:
    """Old settings.json files that stored mic_device as a bare string load gracefully."""
    import json
    import tempfile
    from pathlib import Path
    from teams_transcriber.config import load_settings
    from teams_transcriber.paths import AppPaths

    with tempfile.TemporaryDirectory() as tmp_path:
        paths = AppPaths(root=tmp_path)
        paths.ensure_dirs()
        settings_path = paths.config_dir / "settings.json"
        settings_path.write_text(
            json.dumps({"audio": {"mic_device": "{old-string-id}"}}),
            encoding="utf-8",
        )
        s = load_settings(paths)
        # Legacy str values are not dicts → treated as None.
        assert s.audio_mic_device is None
```

- [ ] **Step 2.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/test_config.py -k audio_device -v
```

- [ ] **Step 2.3: Implement**

In `src/teams_transcriber/config.py`, add `audio_mic_device` and `audio_loopback_device` properties. Replace the existing `mic_device` / `loopback_device` properties with versions that read the new dict format and remain backwards-compatible:

```python
    # --- audio
    @property
    def audio_mic_device(self) -> dict | None:
        value = self._raw["audio"].get("mic_device")
        return value if isinstance(value, dict) else None

    @property
    def audio_loopback_device(self) -> dict | None:
        value = self._raw["audio"].get("loopback_device")
        return value if isinstance(value, dict) else None

    # Backwards-compatible legacy accessors (return the id from the new dict, or None).
    @property
    def mic_device(self) -> str | None:
        d = self.audio_mic_device
        return d["id"] if d is not None else None

    @property
    def loopback_device(self) -> str | None:
        d = self.audio_loopback_device
        return d["id"] if d is not None else None
```

- [ ] **Step 2.4: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/test_config.py -v
```

- [ ] **Step 2.5: Commit**

```powershell
git add src/teams_transcriber/config.py tests/test_config.py
git commit -m "feat(config): audio device settings now {id,name} dict with legacy str→None fallback"
```

---

## Task 3: `NoAudioDevicesError` + `RecordingDeviceFallback` event

**Files:**
- Modify: `src/teams_transcriber/events.py`
- Modify: `src/teams_transcriber/audio/source.py`
- Test: `tests/test_events.py`

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_events.py`:

```python
def test_recording_device_fallback_event_round_trip() -> None:
    from teams_transcriber.events import RecordingDeviceFallback

    evt = RecordingDeviceFallback(recording_id=7, channel="microphone", requested_name="Sony WH-1000")
    assert evt.recording_id == 7
    assert evt.channel == "microphone"
    assert evt.requested_name == "Sony WH-1000"


def test_no_audio_devices_error_is_exception() -> None:
    from teams_transcriber.audio.source import NoAudioDevicesError

    assert issubclass(NoAudioDevicesError, Exception)
    err = NoAudioDevicesError("no devices")
    assert str(err) == "no devices"
```

- [ ] **Step 3.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/test_events.py -k "fallback or no_audio" -v
```

- [ ] **Step 3.3: Implement event**

In `src/teams_transcriber/events.py`, append after the existing `LiveTranscriptionDegraded`:

```python
@dataclass(slots=True, frozen=True)
class RecordingDeviceFallback(Event):
    recording_id: int
    channel: str          # "microphone" or "system audio"
    requested_name: str   # name of the saved-but-unavailable device
```

- [ ] **Step 3.4: Implement exception**

In `src/teams_transcriber/audio/source.py`, near the top after the imports:

```python
class NoAudioDevicesError(RuntimeError):
    """Raised when neither a saved nor default audio device is available."""
```

- [ ] **Step 3.5: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/test_events.py -v
```

- [ ] **Step 3.6: Commit**

```powershell
git add src/teams_transcriber/events.py src/teams_transcriber/audio/source.py tests/test_events.py
git commit -m "feat(events): add RecordingDeviceFallback event + NoAudioDevicesError"
```

---

## Task 4: `RealAudioSource.from_settings()` factory

**Files:**
- Modify: `src/teams_transcriber/audio/source.py`
- Test: `tests/audio/test_source.py`

- [ ] **Step 4.1: Write the failing test**

Append to `tests/audio/test_source.py`:

```python
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
    """When saved id doesn't exist but name matches, use the name match and report fallback."""
    from teams_transcriber.audio.source import RealAudioSource

    class _Dev:
        def __init__(self, id_, name): self.id = id_; self.name = name

    fake_mics = [_Dev("{mic-new}", "Sony Headset")]   # name still matches the saved one
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
    # Name-match wasn't a fallback (the device exists); only the ID changed silently.
    # The fallback report fires when we DROP from saved to default.
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
```

- [ ] **Step 4.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/audio/test_source.py -k from_settings -v
```

- [ ] **Step 4.3: Implement**

In `src/teams_transcriber/audio/source.py`, add this dataclass and class method on `RealAudioSource`:

```python
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

# Add near the top of the file with the other dataclasses / classes.
@dataclass(slots=True)
class _DeviceResolution:
    mic: Any
    loopback: Any
    fallbacks: list[tuple[str, str]]   # list of (channel, requested_name) pairs that fell back to default


class RealAudioSource:
    # ... existing __init__ unchanged ...

    @classmethod
    def from_settings(cls, settings) -> "RealAudioSource":
        """Construct a RealAudioSource using the user's saved device choices, with
        the saved-id → saved-name → Windows-default ladder.

        Raises NoAudioDevicesError if no usable device is available.
        Side effect: stores `.device_fallbacks` (list of (channel, requested_name))
        so the caller can publish RecordingDeviceFallback events.
        """
        import soundcard

        all_mics = soundcard.all_microphones(exclude_monitors=True)
        all_speakers = soundcard.all_speakers()
        default_mic = soundcard.default_microphone() if all_mics else None
        default_speaker = soundcard.default_speaker() if all_speakers else None

        resolution = cls._resolve_devices(
            settings,
            all_mics=all_mics,
            all_speakers=all_speakers,
            get_microphone=soundcard.get_microphone,
            default_mic=default_mic,
            default_speaker=default_speaker,
        )
        # The loopback "device" is the speaker turned into a loopback mic.
        loopback = soundcard.get_microphone(resolution.loopback.id, include_loopback=True)
        instance = cls(mic_device=resolution.mic, loopback_device=loopback)
        instance.device_fallbacks = resolution.fallbacks  # consumed by Pipeline
        return instance

    @staticmethod
    def _resolve_devices(
        settings,
        *,
        all_mics: list,
        all_speakers: list,
        get_microphone: Callable[..., Any],
        default_mic: Any,
        default_speaker: Any,
    ) -> _DeviceResolution:
        fallbacks: list[tuple[str, str]] = []

        def _pick(saved: dict | None, all_devs: list, default_dev, channel_label: str):
            # Try saved id, then name, then default.
            if saved is not None:
                saved_id = saved.get("id")
                saved_name = saved.get("name")
                if saved_id:
                    by_id = next((d for d in all_devs if d.id == saved_id), None)
                    if by_id is not None:
                        return by_id
                if saved_name:
                    by_name = next((d for d in all_devs if d.name == saved_name), None)
                    if by_name is not None:
                        return by_name
                # Fell off the ladder — record the fallback.
                fallbacks.append((channel_label, saved_name or saved_id or "<unknown>"))
            return default_dev

        mic = _pick(settings.audio_mic_device, all_mics, default_mic, "microphone")
        loop = _pick(settings.audio_loopback_device, all_speakers, default_speaker, "system audio")
        if mic is None or loop is None:
            raise NoAudioDevicesError(
                "No audio devices available — check Settings → Audio.",
            )
        return _DeviceResolution(mic=mic, loopback=loop, fallbacks=fallbacks)
```

Also add the `device_fallbacks` field default in `__init__`:

```python
class RealAudioSource:
    def __init__(self, *, mic_device, loopback_device, chunk_frames=CAPTURE_BLOCK_FRAMES, queue_size=64) -> None:
        # ... existing body unchanged ...
        self.device_fallbacks: list[tuple[str, str]] = []
```

Keep the existing `from_default_devices` factory but have it delegate:

```python
    @classmethod
    def from_default_devices(cls) -> "RealAudioSource":
        # Backwards-compat shim — equivalent to from_settings with no saved devices.
        class _NoneSettings:
            audio_mic_device = None
            audio_loopback_device = None
        return cls.from_settings(_NoneSettings())
```

- [ ] **Step 4.4: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/audio/test_source.py -v
```

- [ ] **Step 4.5: Commit**

```powershell
git add src/teams_transcriber/audio/source.py tests/audio/test_source.py
git commit -m "feat(audio): RealAudioSource.from_settings() with id→name→default ladder"
```

---

## Task 5: `pycaw` dependency + `wasapi_sessions.py`

**Files:**
- Modify: `pyproject.toml`
- Create: `src/teams_transcriber/audio/wasapi_sessions.py`
- Create: `tests/audio/test_wasapi_sessions.py`

- [ ] **Step 5.1: Add pycaw dependency**

Edit `pyproject.toml`: in `[project] dependencies`, append:

```
    "pycaw>=20231007",
```

Then run `uv sync` to install it:

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" sync
```

- [ ] **Step 5.2: Write the failing test**

Create `tests/audio/test_wasapi_sessions.py`:

```python
"""Tests for the WASAPI session probe (all pycaw calls mocked)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


def test_returns_empty_set_when_pycaw_missing(monkeypatch):
    """If pycaw can't be imported, the probe degrades to empty set."""
    monkeypatch.setitem(sys.modules, "pycaw", None)
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", None)
    # Drop cached import so the next call re-imports.
    sys.modules.pop("teams_transcriber.audio.wasapi_sessions", None)
    from teams_transcriber.audio import wasapi_sessions
    assert wasapi_sessions.teams_active_capture_pids() == set()


def test_returns_pids_for_active_teams_sessions(monkeypatch):
    """When a Teams process holds an active capture session, return its PID."""
    sys.modules.pop("teams_transcriber.audio.wasapi_sessions", None)
    from teams_transcriber.audio import wasapi_sessions

    # Mock the internal _enumerate_active_capture_sessions helper.
    fake_sessions = [
        # (pid, process_name, state_active)
        (1234, "ms-teams.exe", True),
        (5678, "ms-teams.exe", False),  # inactive — should be skipped
        (9012, "spotify.exe", True),    # not Teams — should be skipped
        (3456, "Teams.exe", True),      # classic Teams — should match
    ]

    def fake_enum():
        return fake_sessions

    monkeypatch.setattr(wasapi_sessions, "_enumerate_active_capture_sessions", fake_enum)
    assert wasapi_sessions.teams_active_capture_pids() == {1234, 3456}


def test_returns_empty_set_when_enumeration_raises(monkeypatch):
    """If the COM call throws, return empty set + log a warning."""
    sys.modules.pop("teams_transcriber.audio.wasapi_sessions", None)
    from teams_transcriber.audio import wasapi_sessions

    def boom():
        raise OSError("COM not initialized")

    monkeypatch.setattr(wasapi_sessions, "_enumerate_active_capture_sessions", boom)
    assert wasapi_sessions.teams_active_capture_pids() == set()
```

- [ ] **Step 5.3: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/audio/test_wasapi_sessions.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 5.4: Implement**

Create `src/teams_transcriber/audio/wasapi_sessions.py`:

```python
"""WASAPI capture-session probe — used by MeetingWatcher to detect "Teams is
holding the mic" without depending on window titles.

All errors (missing pycaw, COM failures, etc.) degrade to an empty-set return.
The watcher falls back to title pattern matching when this returns empty.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TEAMS_PROCESS_NAMES = {"ms-teams.exe", "teams.exe"}


def teams_active_capture_pids() -> set[int]:
    """Return PIDs of Teams processes currently holding an active mic capture session."""
    try:
        sessions = _enumerate_active_capture_sessions()
    except Exception:
        logger.exception("WASAPI capture-session enumeration failed; falling back to empty set")
        return set()
    return {
        pid for (pid, name, active) in sessions
        if active and name and name.lower() in TEAMS_PROCESS_NAMES
    }


def _enumerate_active_capture_sessions() -> list[tuple[int, str, bool]]:
    """Yield (pid, process_name, is_active) for every WASAPI capture session.

    Wrapped in its own function so tests can monkey-patch this and avoid the
    actual COM round-trip. Returns an empty list if pycaw isn't available.
    """
    try:
        import comtypes
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.pycaw import (
            EDataFlow,
            ERole,
            IAudioSessionManager2,
            IMMDeviceEnumerator,
            AudioSession,
        )
    except Exception:
        logger.warning("pycaw not available; WASAPI session probe disabled")
        return []

    results: list[tuple[int, str, bool]] = []
    try:
        comtypes.CoInitialize()
        device_enum = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator,
            IMMDeviceEnumerator,
            comtypes.CLSCTX_ALL,
        )
        capture_dev = device_enum.GetDefaultAudioEndpoint(
            EDataFlow.eCapture.value, ERole.eConsole.value,
        )
        mgr = capture_dev.Activate(
            IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None,
        )
        mgr_obj = mgr.QueryInterface(IAudioSessionManager2)
        enum = mgr_obj.GetSessionEnumerator()
        count = enum.GetCount()
        for i in range(count):
            ctrl = enum.GetSession(i)
            session = AudioSession(ctrl)
            try:
                pid = session.ProcessId
                state = session.State        # 0 inactive, 1 active, 2 expired
                proc = session.Process       # psutil-like Process or None
                name = (proc.name() if proc else "") or ""
                results.append((pid, name, state == 1))
            except Exception:
                logger.exception("Skipping a WASAPI session that failed to read")
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass
    return results
```

- [ ] **Step 5.5: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/audio/test_wasapi_sessions.py -v
```

If the import fails because of a different `pycaw` API surface than expected, narrow the failure: the test only verifies that `_enumerate_active_capture_sessions` is patchable. Production code can stay as a best-effort, since the production path also wraps everything in try/except.

- [ ] **Step 5.6: Commit**

```powershell
git add pyproject.toml uv.lock src/teams_transcriber/audio/wasapi_sessions.py tests/audio/test_wasapi_sessions.py
git commit -m "feat(audio): WASAPI capture-session probe for Teams detection (pycaw)"
```

---

## Task 6: `MeetingWatcher` hybrid detection

**Files:**
- Modify: `src/teams_transcriber/meeting_watcher.py`
- Test: `tests/test_meeting_watcher.py`

- [ ] **Step 6.1: Write the failing test**

Append to `tests/test_meeting_watcher.py`:

```python
def test_watcher_uses_audio_probe_when_non_empty() -> None:
    """When audio_session_probe returns Teams PIDs, the watcher detects a meeting
    regardless of whether the title pattern matches."""
    from teams_transcriber.events import EventBus, MeetingDetected
    from teams_transcriber.meeting_watcher import MeetingWatcher, WindowInfo

    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    # Window with an unrecognizable title — title patterns alone would never catch it.
    windows = [WindowInfo(pid=4321, process_name="ms-teams.exe", title="Random Meeting #2847391")]

    watcher = MeetingWatcher(
        bus=bus,
        current_windows=lambda: windows,
        title_patterns=[],
        audio_session_probe=lambda: {4321},
        debounce_polls=1,
        poll_interval_ms=10,
    )
    watcher.step()
    assert len(detected) == 1
    assert detected[0].window_title == "Random Meeting #2847391"


def test_watcher_falls_back_to_title_when_probe_empty() -> None:
    """When audio_session_probe returns empty, title-pattern matching still runs."""
    from teams_transcriber.events import EventBus, MeetingDetected
    from teams_transcriber.meeting_watcher import MeetingWatcher, WindowInfo

    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    windows = [WindowInfo(pid=4321, process_name="ms-teams.exe",
                          title="Meeting in progress | Microsoft Teams")]

    watcher = MeetingWatcher(
        bus=bus,
        current_windows=lambda: windows,
        title_patterns=["Meeting in progress | Microsoft Teams"],
        audio_session_probe=lambda: set(),
        debounce_polls=1,
        poll_interval_ms=10,
    )
    watcher.step()
    assert len(detected) == 1


def test_watcher_probe_skips_nav_view_titles() -> None:
    """Even when a Teams PID is reported as active, ignore Calendar/Chat/etc. windows."""
    from teams_transcriber.events import EventBus, MeetingDetected
    from teams_transcriber.meeting_watcher import MeetingWatcher, WindowInfo

    bus = EventBus()
    detected: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, detected.append)

    # The PID is reported as having a capture session, but its only windows are nav views.
    windows = [
        WindowInfo(pid=4321, process_name="ms-teams.exe", title="Calendar | Calendar | Microsoft Teams"),
        WindowInfo(pid=4321, process_name="ms-teams.exe", title="Chat | Blake | Microsoft Teams"),
    ]

    watcher = MeetingWatcher(
        bus=bus,
        current_windows=lambda: windows,
        title_patterns=[],
        audio_session_probe=lambda: {4321},
        debounce_polls=1,
        poll_interval_ms=10,
    )
    watcher.step()
    assert detected == []
```

- [ ] **Step 6.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/test_meeting_watcher.py -v
```

- [ ] **Step 6.3: Implement**

In `src/teams_transcriber/meeting_watcher.py`:

1. Add imports near the top:

```python
from teams_transcriber.audio.wasapi_sessions import teams_active_capture_pids
```

2. Update `__init__` to accept `audio_session_probe`:

```python
    def __init__(
        self,
        bus: EventBus,
        current_windows: Callable[[], list[WindowInfo]],
        title_patterns: list[str],
        debounce_polls: int = 2,
        poll_interval_ms: int = 2000,
        audio_session_probe: Callable[[], set[int]] = teams_active_capture_pids,
    ) -> None:
        # ... existing assignments ...
        self._audio_session_probe = audio_session_probe
```

3. Rewrite `_find_meeting_window` to use hybrid logic:

```python
    def _find_meeting_window(self, windows: list[WindowInfo]) -> WindowInfo | None:
        """Hybrid detection: WASAPI audio session probe first, title patterns as fallback.

        1. WASAPI tier: if any Teams PID is holding an active capture session,
           find a real meeting window for one of those PIDs (excluding nav views).
        2. Title tier (fallback): the existing title pattern + smart-fallback logic.
        """
        try:
            active_pids = self._audio_session_probe()
        except Exception:
            logger.exception("audio_session_probe raised; treating as no signal")
            active_pids = set()

        if active_pids:
            # Find a non-nav-view window for any of those PIDs.
            candidates: list[WindowInfo] = []
            for w in windows:
                if w.pid not in active_pids:
                    continue
                if w.process_name.lower() not in TEAMS_PROCESS_NAMES:
                    continue
                title_lower = w.title.lower()
                if title_lower in TEAMS_NAV_VIEW_TITLES:
                    continue
                if any(title_lower.startswith(p) for p in TEAMS_NAV_VIEW_PREFIXES):
                    continue
                candidates.append(w)
            if candidates:
                # Prefer the candidate with the longest title (usually the most descriptive).
                return max(candidates, key=lambda w: len(w.title))

        # Title-pattern fallback (existing logic).
        for w in windows:
            if w.process_name.lower() not in TEAMS_PROCESS_NAMES:
                continue
            title_lower = w.title.lower()
            if any(p in title_lower for p in self._title_patterns):
                return w
            if title_lower in TEAMS_NAV_VIEW_TITLES:
                continue
            if any(title_lower.startswith(p) for p in TEAMS_NAV_VIEW_PREFIXES):
                continue
            if "| microsoft teams" in title_lower or "microsoft teams call" in title_lower:
                return w
        return None
```

- [ ] **Step 6.4: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/test_meeting_watcher.py -v
```

- [ ] **Step 6.5: Commit**

```powershell
git add src/teams_transcriber/meeting_watcher.py tests/test_meeting_watcher.py
git commit -m "feat(detection): hybrid WASAPI-session + title-pattern meeting detection"
```

---

## Task 7: Pipeline gating + error handling

**Files:**
- Modify: `src/teams_transcriber/pipeline.py`
- Test: `tests/test_phase6_pipeline.py` (new)

- [ ] **Step 7.1: Write the failing test**

Create `tests/test_phase6_pipeline.py`:

```python
"""Phase 6 pipeline integration tests."""

from __future__ import annotations

import numpy as np
import pytest

from teams_transcriber.audio.source import FakeAudioSource, NoAudioDevicesError
from teams_transcriber.config import load_settings
from teams_transcriber.events import EventBus, RecordingDeviceFallback, RecordingFailed
from teams_transcriber.paths import AppPaths
from teams_transcriber.pipeline import Pipeline
from teams_transcriber.storage import build_database


def _make_env(tmp_path):
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    settings = load_settings(paths)
    return paths, db, settings


class _NoopTranscriber:
    def transcribe(self, rid: int) -> None: pass


class _NoopSummarizer:
    def summarize(self, rid: int, *, api_key) -> None: pass


def test_pipeline_skips_live_transcriber_when_disabled(tmp_path, monkeypatch) -> None:
    """When transcription.live_enabled is False, no LiveTranscriber is created."""
    paths, db, settings = _make_env(tmp_path)
    bus = EventBus()
    mic = np.zeros(48_000, dtype=np.float32)
    loop = np.zeros(48_000, dtype=np.float32)
    source = FakeAudioSource(mic, loop)

    instantiated: list[str] = []

    class _SpyLive:
        def __init__(self, *_a, **_kw):
            instantiated.append("created")
        def start(self, *_a, **_kw): pass
        def feed(self, *_a, **_kw): pass
        def flush_and_stop(self): pass

    monkeypatch.setattr("teams_transcriber.pipeline.LiveTranscriber", _SpyLive)
    assert settings.transcription_live_enabled is False  # confirm default

    p = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: source,
        meeting_watcher=None,
        transcriber=_NoopTranscriber(),
        summarizer=_NoopSummarizer(),
    )
    p.start_manual(detected_title="t")
    source.run_until_exhausted()
    p.stop_manual()
    p.shutdown()
    db.close()

    assert instantiated == []  # no LiveTranscriber when disabled


def test_pipeline_creates_live_transcriber_when_enabled(tmp_path, monkeypatch) -> None:
    """When transcription.live_enabled is True, the LiveTranscriber IS created."""
    paths, db, settings = _make_env(tmp_path)
    settings._raw["transcription"]["live_enabled"] = True
    bus = EventBus()
    mic = np.zeros(48_000, dtype=np.float32)
    loop = np.zeros(48_000, dtype=np.float32)
    source = FakeAudioSource(mic, loop)

    instantiated: list[str] = []

    class _SpyLive:
        def __init__(self, *_a, **_kw):
            instantiated.append("created")
        def start(self, *_a, **_kw): pass
        def feed(self, *_a, **_kw): pass
        def flush_and_stop(self): pass

    monkeypatch.setattr("teams_transcriber.pipeline.LiveTranscriber", _SpyLive)

    p = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: source,
        meeting_watcher=None,
        transcriber=_NoopTranscriber(),
        summarizer=_NoopSummarizer(),
    )
    p.start_manual(detected_title="t")
    source.run_until_exhausted()
    p.stop_manual()
    p.shutdown()
    db.close()

    assert instantiated == ["created"]


def test_pipeline_handles_no_audio_devices(tmp_path) -> None:
    """When the source factory raises NoAudioDevicesError, publish RecordingFailed."""
    paths, db, settings = _make_env(tmp_path)
    bus = EventBus()
    failed: list[RecordingFailed] = []
    bus.subscribe(RecordingFailed, failed.append)

    def explode():
        raise NoAudioDevicesError("No audio devices available — check Settings → Audio.")

    p = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=explode,
        meeting_watcher=None,
        transcriber=_NoopTranscriber(),
        summarizer=_NoopSummarizer(),
    )
    rid = p.start_manual(detected_title="t")
    assert rid == -1
    assert len(failed) == 1
    assert "audio devices" in failed[0].error_message.lower()
    p.shutdown()
    db.close()


def test_pipeline_republishes_device_fallbacks(tmp_path) -> None:
    """When source.device_fallbacks is non-empty, publish RecordingDeviceFallback events."""
    paths, db, settings = _make_env(tmp_path)
    bus = EventBus()
    fallbacks: list[RecordingDeviceFallback] = []
    bus.subscribe(RecordingDeviceFallback, fallbacks.append)

    mic = np.zeros(48_000, dtype=np.float32)
    loop = np.zeros(48_000, dtype=np.float32)
    source = FakeAudioSource(mic, loop)
    source.device_fallbacks = [("microphone", "Vanished Mic")]

    p = Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: source,
        meeting_watcher=None,
        transcriber=_NoopTranscriber(),
        summarizer=_NoopSummarizer(),
    )
    rid = p.start_manual(detected_title="t")
    source.run_until_exhausted()
    p.stop_manual()
    p.shutdown()
    db.close()

    assert len(fallbacks) == 1
    assert fallbacks[0].channel == "microphone"
    assert fallbacks[0].requested_name == "Vanished Mic"
    assert fallbacks[0].recording_id == rid
```

Add `device_fallbacks` field to `FakeAudioSource` for test compat (in source.py — see step 7.3 implementation note).

- [ ] **Step 7.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/test_phase6_pipeline.py -v
```

- [ ] **Step 7.3: Implement**

First, add `device_fallbacks` to `FakeAudioSource` for parity with `RealAudioSource`. In `src/teams_transcriber/audio/source.py` `FakeAudioSource.__init__`:

```python
class FakeAudioSource:
    def __init__(self, mic_samples: np.ndarray, loopback_samples: np.ndarray) -> None:
        # ... existing assignments ...
        self.device_fallbacks: list[tuple[str, str]] = []
```

Now in `src/teams_transcriber/pipeline.py`:

1. Add import for the new exception + event:

```python
from teams_transcriber.audio.source import NoAudioDevicesError
from teams_transcriber.events import (
    # ... existing imports ...
    RecordingDeviceFallback,
)
```

2. Rewrite `_start_recorder` to gate live + handle errors:

```python
    def _start_recorder(self, *, source_type: str, detected_title: str | None) -> int:
        if self._recorder is not None:
            logger.warning("recorder already running; ignoring duplicate start")
            return -1

        try:
            source = self._audio_source_factory()
        except NoAudioDevicesError as exc:
            logger.warning("recording start failed: %s", exc)
            self._bus.publish(RecordingFailed(
                recording_id=-1,
                error_message=str(exc),
            ))
            return -1
        except Exception as exc:
            logger.exception("audio source factory failed")
            self._bus.publish(RecordingFailed(
                recording_id=-1,
                error_message=f"Audio capture could not start: {exc}",
            ))
            return -1

        live = None
        audio_chunk_callback = None
        if self._settings.transcription_live_enabled:
            live = LiveTranscriber(
                bus=self._bus, db=self._db, settings=self._settings,
            )
            self._live_transcriber = live

            def _on_audio_chunk(chunk: np.ndarray) -> None:
                mic = chunk[:, 0]
                loop = chunk[:, 1]
                live.feed(Channel.ME, mic)
                live.feed(Channel.OTHERS, loop)
            audio_chunk_callback = _on_audio_chunk

        self._recorder = Recorder(
            bus=self._bus, db=self._db, paths=self._paths,
            settings=self._settings, audio_source=source,
            audio_chunk_callback=audio_chunk_callback,
        )
        rec_id = self._recorder.start(
            source_type=source_type, detected_title=detected_title,
        )

        if rec_id > 0:
            # Republish any fallbacks the source recorded during construction.
            for channel, requested_name in getattr(source, "device_fallbacks", []):
                self._bus.publish(RecordingDeviceFallback(
                    recording_id=rec_id, channel=channel, requested_name=requested_name,
                ))
            if live is not None:
                live.start(rec_id)
        return rec_id
```

- [ ] **Step 7.4: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/test_phase6_pipeline.py tests/test_pipeline.py -v
```

- [ ] **Step 7.5: Commit**

```powershell
git add src/teams_transcriber/pipeline.py src/teams_transcriber/audio/source.py tests/test_phase6_pipeline.py
git commit -m "feat(pipeline): gate LiveTranscriber on live_enabled; handle NoAudioDevicesError + fallbacks"
```

---

## Task 8: Settings dialog Audio tab

**Files:**
- Modify: `src/teams_transcriber/ui/settings_dialog.py`
- Test: `tests/ui/test_settings_dialog.py`

- [ ] **Step 8.1: Write the failing test**

Append to `tests/ui/test_settings_dialog.py`:

```python
def test_settings_dialog_audio_tab_round_trip(tmp_path, qapp, monkeypatch) -> None:
    """Selecting a mic + loopback in the Audio tab persists as {id, name} dicts."""
    from teams_transcriber.config import load_settings
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.ui.settings_dialog import SettingsDialog

    class _Dev:
        def __init__(self, id_, name): self.id = id_; self.name = name

    fake_mics = [_Dev("{mic-a}", "Mic A"), _Dev("{mic-b}", "Mic B")]
    fake_speakers = [_Dev("{spk-a}", "Spk A")]

    monkeypatch.setattr(
        "teams_transcriber.ui.settings_dialog._enumerate_microphones",
        lambda: fake_mics,
    )
    monkeypatch.setattr(
        "teams_transcriber.ui.settings_dialog._enumerate_speakers",
        lambda: fake_speakers,
    )

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    # Select Mic B from the dropdown.
    dlg._mic_combo.setCurrentIndex(2)  # 0 = Default, 1 = Mic A, 2 = Mic B
    dlg._loopback_combo.setCurrentIndex(1)  # 0 = Default, 1 = Spk A
    dlg._on_accept()
    reloaded = load_settings(paths)
    assert reloaded.audio_mic_device == {"id": "{mic-b}", "name": "Mic B"}
    assert reloaded.audio_loopback_device == {"id": "{spk-a}", "name": "Spk A"}


def test_settings_dialog_audio_default_round_trips(tmp_path, qapp, monkeypatch) -> None:
    """Choosing 'Use Windows default' persists as None."""
    from teams_transcriber.config import load_settings
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(
        "teams_transcriber.ui.settings_dialog._enumerate_microphones",
        lambda: [],
    )
    monkeypatch.setattr(
        "teams_transcriber.ui.settings_dialog._enumerate_speakers",
        lambda: [],
    )

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    settings = load_settings(paths)
    # Pre-seed a saved device that the dialog should clear if the user picks "Use Windows default".
    settings._raw["audio"]["mic_device"] = {"id": "{old}", "name": "Old Mic"}
    from teams_transcriber.config import save_settings
    save_settings(paths, settings)

    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg._mic_combo.setCurrentIndex(0)  # 0 = Default
    dlg._on_accept()

    reloaded = load_settings(paths)
    assert reloaded.audio_mic_device is None
```

- [ ] **Step 8.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/ui/test_settings_dialog.py -k "audio_tab or audio_default" -v
```

- [ ] **Step 8.3: Implement**

In `src/teams_transcriber/ui/settings_dialog.py`:

1. Add module-level enumeration helpers at the top so tests can monkeypatch:

```python
def _enumerate_microphones() -> list:
    try:
        import soundcard
        return list(soundcard.all_microphones(exclude_monitors=True))
    except Exception:
        return []


def _enumerate_speakers() -> list:
    try:
        import soundcard
        return list(soundcard.all_speakers())
    except Exception:
        return []
```

2. Add a new `_build_audio_tab` method on the dialog class:

```python
    def _build_audio_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self._mic_combo = QComboBox()
        self._loopback_combo = QComboBox()
        self._mic_combo.addItem("Use Windows default", userData=None)
        for mic in _enumerate_microphones():
            self._mic_combo.addItem(mic.name, userData={"id": mic.id, "name": mic.name})
        self._loopback_combo.addItem("Use Windows default", userData=None)
        for spk in _enumerate_speakers():
            self._loopback_combo.addItem(spk.name, userData={"id": spk.id, "name": spk.name})

        # Preselect from settings.
        saved_mic = self._settings.audio_mic_device
        if saved_mic is not None:
            for i in range(self._mic_combo.count()):
                if self._mic_combo.itemData(i) and self._mic_combo.itemData(i).get("id") == saved_mic.get("id"):
                    self._mic_combo.setCurrentIndex(i)
                    break
        saved_loop = self._settings.audio_loopback_device
        if saved_loop is not None:
            for i in range(self._loopback_combo.count()):
                if self._loopback_combo.itemData(i) and self._loopback_combo.itemData(i).get("id") == saved_loop.get("id"):
                    self._loopback_combo.setCurrentIndex(i)
                    break

        form.addRow("Microphone:", self._mic_combo)
        form.addRow("System audio source:", self._loopback_combo)

        # Move retention + bitrate here if they currently live elsewhere.
        from PySide6.QtWidgets import QSpinBox
        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(0, 365)
        self.retention_spin.setValue(self._settings.audio_retention_days)
        self.retention_spin.setSuffix(" days")
        form.addRow("Retention:", self.retention_spin)

        return w
```

3. Add the tab to the dialog's tab widget construction (find where other tabs are added, e.g. `tabs.addTab(self._build_detection_tab(), "Detection")`):

```python
        tabs.addTab(self._build_audio_tab(), "Audio")
```

4. Extend `_on_accept` to read the dropdowns:

```python
        # --- Audio (Phase 6) ---
        s._raw["audio"]["mic_device"] = self._mic_combo.currentData()
        s._raw["audio"]["loopback_device"] = self._loopback_combo.currentData()
        s._raw["audio"]["retention_days"] = self.retention_spin.value()
```

If the existing `_on_accept` already writes `retention_days` from the General tab, remove that line — it's now owned by the Audio tab.

Make sure `QComboBox` is imported at the top of settings_dialog.py.

- [ ] **Step 8.4: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/ui/test_settings_dialog.py -v
```

- [ ] **Step 8.5: Commit**

```powershell
git add src/teams_transcriber/ui/settings_dialog.py tests/ui/test_settings_dialog.py
git commit -m "feat(ui): add Audio tab to Settings with mic + loopback dropdowns"
```

---

## Task 9: Settings dialog `live_enabled` checkbox

**Files:**
- Modify: `src/teams_transcriber/ui/settings_dialog.py`
- Test: `tests/ui/test_settings_dialog.py`

- [ ] **Step 9.1: Write the failing test**

Append to `tests/ui/test_settings_dialog.py`:

```python
def test_settings_dialog_live_enabled_round_trip(tmp_path, qapp) -> None:
    from teams_transcriber.config import load_settings
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.ui.settings_dialog import SettingsDialog

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    settings = load_settings(paths)
    assert settings.transcription_live_enabled is False  # default

    dlg = SettingsDialog(settings, paths)
    dlg._live_enabled_check.setChecked(True)
    dlg._on_accept()

    reloaded = load_settings(paths)
    assert reloaded.transcription_live_enabled is True
```

- [ ] **Step 9.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/ui/test_settings_dialog.py -k live_enabled -v
```

- [ ] **Step 9.3: Implement**

In `src/teams_transcriber/ui/settings_dialog.py`, find the existing `_build_transcription_tab` method (or equivalent). Add a checkbox to its layout:

```python
from PySide6.QtWidgets import QCheckBox

# Inside _build_transcription_tab, after the existing form rows:
        self._live_enabled_check = QCheckBox("Stream transcription during recording (experimental)")
        self._live_enabled_check.setChecked(self._settings.transcription_live_enabled)
        form.addRow("", self._live_enabled_check)
```

In `_on_accept`, add a write after the existing transcription writes:

```python
        s._raw["transcription"]["live_enabled"] = self._live_enabled_check.isChecked()
```

- [ ] **Step 9.4: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/ui/test_settings_dialog.py -v
```

- [ ] **Step 9.5: Commit**

```powershell
git add src/teams_transcriber/ui/settings_dialog.py tests/ui/test_settings_dialog.py
git commit -m "feat(ui): add 'Stream transcription during recording' checkbox to Transcription tab"
```

---

## Task 10: WorkspaceWindow placeholder when live disabled

**Files:**
- Modify: `src/teams_transcriber/ui/workspace_window.py`
- Test: `tests/ui/test_workspace_window.py`

- [ ] **Step 10.1: Write the failing test**

Append to `tests/ui/test_workspace_window.py`:

```python
def test_workspace_shows_placeholder_when_live_disabled(env, qapp) -> None:
    """In live recording mode but with live_enabled=False, show a placeholder
    instead of subscribing to LiveSegmentAvailable."""
    paths, db, settings = env
    settings._raw["transcription"]["live_enabled"] = False
    bus = EventBus()
    bridge = QtEventBridge(bus)
    rid = _make_recording(db, status=RecordingStatus.RECORDING)
    win = WorkspaceWindow(
        db=db, recording_id=rid, bridge=bridge, live=True, settings=settings,
    )
    # The transcript view should have 0 items even after a live segment is published.
    bus.publish(LiveSegmentAvailable(
        recording_id=rid,
        segment=TranscriptSegment(
            id=None, recording_id=rid, start_ms=0, end_ms=1500,
            channel=Channel.ME, text="should be ignored",
        ),
    ))
    qapp.processEvents()
    assert win.transcript_view.count() == 0
```

(If the existing test file's `WorkspaceWindow` constructor doesn't take `settings`, the placeholder behavior can read `live_enabled` from `db`-level config indirectly — but simplest is to accept settings explicitly.)

- [ ] **Step 10.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/ui/test_workspace_window.py -v
```

- [ ] **Step 10.3: Implement**

In `src/teams_transcriber/ui/workspace_window.py`, update `WorkspaceWindow.__init__` to accept an optional `settings` parameter and gate live subscription:

```python
    def __init__(
        self,
        *,
        db: Database,
        recording_id: int,
        bridge: QtEventBridge,
        live: bool,
        settings = None,    # NEW — Phase 6
        parent: QWidget | None = None,
    ) -> None:
        # ... existing setup unchanged ...

        # Wire live or past mode.
        live_streaming_enabled = (
            settings is None or settings.transcription_live_enabled
        )
        if live and live_streaming_enabled:
            self._bridge.live_segment_available.connect(self._on_live_segment)
        elif live and not live_streaming_enabled:
            # Show a placeholder card; subscribe to SummaryReady to refresh later.
            self._show_placeholder("Transcription will appear when the meeting ends.")
            self._bridge.summary_ready.connect(self._on_summary_ready_refresh)
        else:
            segments = TranscriptRepo(db).list_for_recording(recording_id)
            self.transcript_view.load_segments(segments)

    def _show_placeholder(self, text: str) -> None:
        from PySide6.QtWidgets import QLabel
        placeholder = QLabel(text)
        placeholder.setStyleSheet("color: #6B7280; padding: 24px; font-size: 13px;")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Replace transcript_view's parent layout content with the placeholder.
        # The simplest approach: leave transcript_view empty and put a label above it.
        # Or: insert the placeholder into the right-side layout above the (empty) transcript view.
        # Use whichever fits the existing layout structure.
        parent_layout = self.transcript_view.parent().layout() if self.transcript_view.parent() else None
        if parent_layout is not None:
            parent_layout.insertWidget(0, placeholder)
            self._placeholder = placeholder

    def _on_summary_ready_refresh(self, evt) -> None:
        if evt.recording_id != self._recording_id:
            return
        segments = TranscriptRepo(self._db).list_for_recording(self._recording_id)
        self.transcript_view.load_segments(segments)
        if hasattr(self, "_placeholder") and self._placeholder is not None:
            self._placeholder.deleteLater()
            self._placeholder = None
```

In `src/teams_transcriber/ui/app.py`, update the call site to pass `settings`:

```python
        win = WorkspaceWindow(
            db=self.db,
            recording_id=recording_id,
            bridge=self.bridge,
            live=live,
            settings=self.settings,
        )
```

- [ ] **Step 10.4: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/ui/test_workspace_window.py -v
```

- [ ] **Step 10.5: Commit**

```powershell
git add src/teams_transcriber/ui/workspace_window.py src/teams_transcriber/ui/app.py tests/ui/test_workspace_window.py
git commit -m "feat(ui): WorkspaceWindow shows placeholder + reloads on SummaryReady when live disabled"
```

---

## Task 11: App error UX + bridge wiring

**Files:**
- Modify: `src/teams_transcriber/ui/qt_bridge.py`
- Modify: `src/teams_transcriber/ui/app.py`
- Test: `tests/ui/test_qt_bridge.py`

- [ ] **Step 11.1: Write the failing test**

Append to `tests/ui/test_qt_bridge.py`:

```python
def test_bridge_emits_recording_device_fallback(qapp) -> None:
    from teams_transcriber.events import EventBus, RecordingDeviceFallback
    from teams_transcriber.ui.qt_bridge import QtEventBridge

    bus = EventBus()
    bridge = QtEventBridge(bus)
    received: list[RecordingDeviceFallback] = []
    bridge.recording_device_fallback.connect(received.append)

    bus.publish(RecordingDeviceFallback(
        recording_id=42, channel="microphone", requested_name="Sony Headset",
    ))
    qapp.processEvents()
    assert received and received[0].requested_name == "Sony Headset"
```

- [ ] **Step 11.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/ui/test_qt_bridge.py -k recording_device_fallback -v
```

- [ ] **Step 11.3: Implement bridge**

In `src/teams_transcriber/ui/qt_bridge.py`, extend imports and signals:

```python
from teams_transcriber.events import (
    # ... existing imports ...
    RecordingDeviceFallback,
)


class QtEventBridge(QObject):
    # ... existing signals ...
    recording_device_fallback = Signal(object)

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = bus
        # ... existing subscribe calls ...
        bus.subscribe(RecordingDeviceFallback, self._on_recording_device_fallback)

    def _on_recording_device_fallback(self, e: RecordingDeviceFallback) -> None:
        self.recording_device_fallback.emit(e)
```

- [ ] **Step 11.4: Wire app handlers**

In `src/teams_transcriber/ui/app.py`:

1. In `App.__init__`, connect the new bridge signal:

```python
        self.bridge.recording_device_fallback.connect(self._on_recording_device_fallback)
```

2. Add the handler method:

```python
    def _on_recording_device_fallback(self, evt) -> None:
        channel_label = "microphone" if evt.channel == "microphone" else "system audio source"
        show_in_app_toast(
            f"Saved {channel_label} not connected",
            f"'{evt.requested_name}' is not available — using Windows default. "
            "Choose a different device in Settings → Audio.",
            action_label="Open Settings",
            action_callback=self._open_settings_audio_tab,
        )

    def _open_settings_audio_tab(self) -> None:
        # Open Settings and jump to the Audio tab if possible.
        dlg = SettingsDialog(
            self.settings, self.paths,
            hotkey_reload_callback=self._on_hotkey_reload,
            parent=self.window,
        )
        # Try to switch to the Audio tab if the dialog exposes a tab widget.
        for child in dlg.findChildren(__import__("PySide6.QtWidgets", fromlist=["QTabWidget"]).QTabWidget):
            for i in range(child.count()):
                if child.tabText(i) == "Audio":
                    child.setCurrentIndex(i)
                    break
        dlg.saved.connect(self._refresh_history)
        dlg.exec()
```

3. Extend `_on_recording_failed` so it offers the "Open Settings" action when the failure is the no-devices case:

```python
    def _on_recording_failed(self, evt: RecordingFailed) -> None:
        self.tray.set_state(TrayState.ERROR)
        msg = evt.error_message
        if "audio devices" in msg.lower():
            show_in_app_toast(
                "Recording failed", msg,
                action_label="Open Settings",
                action_callback=self._open_settings_audio_tab,
            )
        else:
            show_in_app_toast("Recording failed", msg)
        self._refresh_history()
```

- [ ] **Step 11.5: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/ui/test_qt_bridge.py -v
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest -v
```

Full suite should be green.

- [ ] **Step 11.6: Commit**

```powershell
git add src/teams_transcriber/ui/qt_bridge.py src/teams_transcriber/ui/app.py tests/ui/test_qt_bridge.py
git commit -m "feat(ui): RecordingDeviceFallback + 'Open Settings' deep-link for no-devices error"
```

---

## Task 12: Manual verification checklist

**Files:**
- Create: `docs/superpowers/checklists/2026-05-19-phase-6-verification.md`

- [ ] **Step 12.1: Run full suite**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest -v
```

Expect all tests green.

- [ ] **Step 12.2: Write the verification checklist**

Create `docs/superpowers/checklists/2026-05-19-phase-6-verification.md`:

```markdown
# Phase 6 Manual Verification

## Audio device selection

- [ ] Open Settings → Audio. Both dropdowns enumerate real devices.
- [ ] Each dropdown has "Use Windows default" at the top.
- [ ] Pick a specific microphone, save. Restart the app. Settings remembers the choice.
- [ ] Start a manual recording (`ctrl+alt+r`). Confirm via `mmsys.cpl` that the chosen mic shows "in use" indicator.
- [ ] Disconnect the saved mic (e.g. unplug USB or turn off Bluetooth). Press `ctrl+alt+r`. Toast appears: "Saved microphone 'X' not connected — using Windows default."
- [ ] Recording proceeds using the Windows default.

## WASAPI Teams detection

- [ ] In Settings → Detection, remove all title patterns (so title matching can never fire).
- [ ] Start a Teams meeting. Auto-detection still fires (WASAPI session probe catches it).
- [ ] End the meeting. `MeetingEnded` fires.
- [ ] Open a Teams Chat conversation (mic NOT active). Auto-detection does NOT fire.

## live_enabled toggle

- [ ] Default value (fresh install): live_enabled is unchecked in Settings → Transcription.
- [ ] Start a recording. Workspace shows placeholder card: "Transcription will appear when the meeting ends."
- [ ] On `SummaryReady`, the workspace transcript pane populates with the final segments.
- [ ] Check the live_enabled box. Save. Start another recording. Workspace now streams segments live (Phase 5 behavior).

## Error UX

- [ ] On a machine with no audio endpoints (or after disabling them in Device Manager): press `ctrl+alt+r`. Toast appears with "Open Settings" action. Click it → Settings opens on the Audio tab.
- [ ] No partial recording row is created.
```

- [ ] **Step 12.3: Commit**

```powershell
git add docs/superpowers/checklists/2026-05-19-phase-6-verification.md
git commit -m "docs(phase-6): add manual verification checklist"
```

---

## Self-Review Notes

**Spec coverage check** — each spec requirement maps to a task:

- `transcription.live_enabled` setting + property → Task 1.
- Audio device dict settings + legacy-string-loads-as-None → Task 2.
- `NoAudioDevicesError` + `RecordingDeviceFallback` event → Task 3.
- `RealAudioSource.from_settings()` with lookup ladder → Task 4.
- `pycaw` dependency + `wasapi_sessions.py` module + error degradation → Task 5.
- `MeetingWatcher` hybrid detection (WASAPI primary, title fallback) → Task 6.
- Pipeline gates `LiveTranscriber` on `live_enabled` + handles errors + republishes fallback events → Task 7.
- Settings dialog Audio tab + dropdowns + retention move → Task 8.
- Settings dialog "Stream transcription" checkbox → Task 9.
- WorkspaceWindow placeholder + SummaryReady reload → Task 10.
- App `_on_recording_failed` "Open Settings" action + fallback toast + bridge wiring → Task 11.
- Manual verification → Task 12.

**Type / signature consistency:**

- `audio_session_probe: Callable[[], set[int]]` used identically in Task 5 (wasapi_sessions) and Task 6 (MeetingWatcher).
- `RecordingDeviceFallback(recording_id, channel, requested_name)` consistent across Tasks 3, 7, 11.
- `audio_mic_device: dict | None`, `audio_loopback_device: dict | None` consistent across Tasks 2, 4, 8.
- `transcription_live_enabled: bool` consistent across Tasks 1, 7, 9, 10.

**Out-of-scope confirmed** (deferred per spec):

- CPU-only installer flavor (Phase 7+).
- cuDNN cherry-picking (Phase 7+).
- Long-meeting transcript chunking.
- Diarization, calendar correlation, To Do export, auto-update.

**Risk acknowledgments** (carry into review):

- The `pycaw` capture-session enumeration may need adjustment to match its actual API surface. The implementation wraps everything in try/except + returns empty set on failure, so production won't crash even if the COM path is broken — the fallback title-pattern detection kicks in. Tests fully mock the enumeration, so they're agnostic to the API surface.
- The legacy `mic_device` / `loopback_device` `str` properties stay for backwards compatibility (so any code path I don't touch still compiles). They return the `id` field of the new dict, so they still produce a string when a device is saved.
