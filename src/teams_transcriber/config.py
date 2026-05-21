"""Application settings loaded from disk, with defaults baked in."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any

import keyring

from teams_transcriber.paths import AppPaths

KEYRING_SERVICE = "teams-transcriber"
KEYRING_USER_ANTHROPIC = "anthropic_api_key"


# Default settings — load_settings merges any user-provided file on top of this.
DEFAULT_SETTINGS: dict[str, Any] = {
    "general": {
        "auto_launch": True,
        "pause_on_startup": False,
        "auto_check_updates": True,
        "last_update_check": None,
    },
    "audio": {
        "mic_device": None,
        "loopback_device": None,
        "retention_days": 30,
        "bitrate_kbps": 24,
    },
    "detection": {
        "poll_interval_ms": 2000,
        "debounce_polls": 2,
        "title_patterns": [
            "Meeting in progress | Microsoft Teams",
            "Meeting | Microsoft Teams",
            "| Microsoft Teams Call",
            "Meeting with ",
        ],
    },
    "transcription": {
        "model": "large-v3-turbo",
        "compute_type": "int8_float16",
        "language": "en",
        "live_enabled": False,  # Phase 6: live transcription is opt-in.
        "live_dual_channel": False,  # Phase 2 ships post-mode only; live is Phase 2.5.
        "live_flush_interval_ms": 10_000,
        "live_max_wait_ms": 15_000,
    },
    "ai": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "custom_prompt_addendum": "",
        "max_retries": 3,
    },
    "hotkeys": {
        "toggle_manual_recording": "ctrl+alt+r",
        "open_workspace": "ctrl+alt+n",
        "toggle_pause_detection": "ctrl+alt+p",
    },
}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Return `base` with values from `overlay` recursively applied. Mutates a copy."""
    result = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


@dataclass(slots=True)
class Settings:
    """Typed view over the settings dict. New fields go here as the app grows."""

    _raw: dict[str, Any] = field(
        default_factory=lambda: copy.deepcopy(DEFAULT_SETTINGS),
    )

    # --- general
    @property
    def auto_launch(self) -> bool:
        return bool(self._raw["general"]["auto_launch"])

    @auto_launch.setter
    def auto_launch(self, value: bool) -> None:
        self._raw["general"]["auto_launch"] = bool(value)

    @property
    def auto_check_updates(self) -> bool:
        return bool(self._raw["general"].get("auto_check_updates", True))

    @property
    def last_update_check(self) -> str | None:
        v = self._raw["general"].get("last_update_check")
        return v if isinstance(v, str) else None

    # --- audio
    @property
    def audio_mic_device(self) -> dict | None:
        """Saved microphone selection as {id, name} or None for Windows default."""
        value = self._raw["audio"].get("mic_device")
        return value if isinstance(value, dict) else None

    @property
    def audio_loopback_device(self) -> dict | None:
        """Saved system audio (loopback) source as {id, name} or None for default."""
        value = self._raw["audio"].get("loopback_device")
        return value if isinstance(value, dict) else None

    # Backwards-compatible legacy accessors — return the id from the dict, or None.
    @property
    def mic_device(self) -> str | None:
        d = self.audio_mic_device
        return d["id"] if d is not None else None

    @property
    def loopback_device(self) -> str | None:
        d = self.audio_loopback_device
        return d["id"] if d is not None else None

    @property
    def audio_retention_days(self) -> int:
        return int(self._raw["audio"]["retention_days"])

    @audio_retention_days.setter
    def audio_retention_days(self, value: int) -> None:
        self._raw["audio"]["retention_days"] = int(value)

    @property
    def audio_bitrate_kbps(self) -> int:
        return int(self._raw["audio"]["bitrate_kbps"])

    # --- detection
    @property
    def detection_poll_interval_ms(self) -> int:
        return int(self._raw["detection"]["poll_interval_ms"])

    @property
    def detection_debounce_polls(self) -> int:
        return int(self._raw["detection"]["debounce_polls"])

    @property
    def detection_title_patterns(self) -> list[str]:
        return list(self._raw["detection"]["title_patterns"])

    # --- transcription
    @property
    def transcription_model(self) -> str:
        return str(self._raw["transcription"]["model"])

    @property
    def transcription_compute_type(self) -> str:
        return str(self._raw["transcription"]["compute_type"])

    @property
    def transcription_language(self) -> str:
        return str(self._raw["transcription"]["language"])

    @property
    def transcription_live_enabled(self) -> bool:
        return bool(self._raw["transcription"].get("live_enabled", False))

    @property
    def transcription_live_dual_channel(self) -> bool:
        return bool(self._raw["transcription"]["live_dual_channel"])

    @property
    def transcription_live_flush_interval_ms(self) -> int:
        return int(self._raw["transcription"]["live_flush_interval_ms"])

    @property
    def transcription_live_max_wait_ms(self) -> int:
        return int(self._raw["transcription"]["live_max_wait_ms"])

    # --- ai
    @property
    def ai_model(self) -> str:
        return str(self._raw["ai"]["model"])

    @ai_model.setter
    def ai_model(self, value: str) -> None:
        self._raw["ai"]["model"] = value

    @property
    def ai_custom_prompt_addendum(self) -> str:
        return str(self._raw["ai"]["custom_prompt_addendum"])

    @property
    def ai_max_retries(self) -> int:
        return int(self._raw["ai"]["max_retries"])

    # --- hotkeys
    @property
    def hotkeys(self) -> dict[str, str]:
        return dict(self._raw["hotkeys"])

    def anthropic_api_key(self) -> str | None:
        """Resolve the Anthropic API key. Env var wins over keyring (useful for CI/tests)."""
        env = os.environ.get("ANTHROPIC_API_KEY")
        if env:
            return env
        try:
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER_ANTHROPIC)
        except keyring.errors.KeyringError:
            return None

    def to_dict(self) -> dict[str, Any]:
        return _deep_merge(self._raw, {})


def load_settings(paths: AppPaths) -> Settings:
    """Load settings.json from disk; fall back to defaults if missing or malformed."""
    settings_path = paths.config_dir / "settings.json"
    raw = copy.deepcopy(DEFAULT_SETTINGS)
    if settings_path.exists():
        try:
            user = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                raw = _deep_merge(raw, user)
        except (json.JSONDecodeError, OSError):
            # Malformed file — log and fall back to defaults.
            pass
    return Settings(_raw=raw)


def save_settings(paths: AppPaths, settings: Settings) -> None:
    """Persist current settings to settings.json. Creates config_dir if needed."""
    paths.ensure_dirs()
    settings_path = paths.config_dir / "settings.json"
    settings_path.write_text(
        json.dumps(settings.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
