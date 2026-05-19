from __future__ import annotations

import json
from pathlib import Path

import pytest

from teams_transcriber.config import (
    DEFAULT_SETTINGS,
    load_settings,
    save_settings,
)
from teams_transcriber.paths import AppPaths


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT")
    p.ensure_dirs()
    return p


def test_load_returns_defaults_when_file_missing(paths: AppPaths) -> None:
    s = load_settings(paths)
    assert s.ai_model == DEFAULT_SETTINGS["ai"]["model"]
    assert s.detection_poll_interval_ms == DEFAULT_SETTINGS["detection"]["poll_interval_ms"]
    assert s.audio_retention_days == DEFAULT_SETTINGS["audio"]["retention_days"]


def test_save_then_load_round_trips(paths: AppPaths) -> None:
    s = load_settings(paths)
    s.ai_model = "claude-opus-4-7"
    s.audio_retention_days = 60
    save_settings(paths, s)
    again = load_settings(paths)
    assert again.ai_model == "claude-opus-4-7"
    assert again.audio_retention_days == 60


def test_partial_settings_file_merges_with_defaults(paths: AppPaths) -> None:
    """A settings file missing some keys still loads — missing keys come from defaults."""
    settings_path = paths.config_dir / "settings.json"
    settings_path.write_text(json.dumps({"ai": {"model": "claude-haiku-4-5"}}))
    s = load_settings(paths)
    assert s.ai_model == "claude-haiku-4-5"
    # Other defaults still present:
    assert s.detection_poll_interval_ms == DEFAULT_SETTINGS["detection"]["poll_interval_ms"]


def test_malformed_json_falls_back_to_defaults(paths: AppPaths) -> None:
    (paths.config_dir / "settings.json").write_text("not valid json {")
    s = load_settings(paths)
    assert s.ai_model == DEFAULT_SETTINGS["ai"]["model"]


def test_api_key_from_env(paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-env")
    s = load_settings(paths)
    assert s.anthropic_api_key() == "sk-test-env"


def test_api_key_from_keyring(paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import keyring
    from keyring.backend import KeyringBackend

    class FakeKeyring(KeyringBackend):
        priority = 1  # type: ignore[assignment]

        def __init__(self) -> None:
            self._store: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self._store.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self._store[(service, username)] = password

        def delete_password(self, service: str, username: str) -> None:
            self._store.pop((service, username), None)

    fk = FakeKeyring()
    fk.set_password("teams-transcriber", "anthropic_api_key", "sk-test-ring")
    keyring.set_keyring(fk)
    try:
        s = load_settings(paths)
        assert s.anthropic_api_key() == "sk-test-ring"
    finally:
        keyring.set_keyring(keyring.backends.fail.Keyring())  # type: ignore[attr-defined]


def test_api_key_returns_none_when_unset(paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import keyring
    keyring.set_keyring(keyring.backends.fail.Keyring())  # type: ignore[attr-defined]
    s = load_settings(paths)
    assert s.anthropic_api_key() is None


def test_default_settings_immune_to_settings_mutation(paths: AppPaths) -> None:
    """Mutating loaded settings must not affect DEFAULT_SETTINGS."""
    from teams_transcriber.config import DEFAULT_SETTINGS

    s1 = load_settings(paths)
    s1._raw["detection"]["title_patterns"].append("new pattern injected by s1")

    s2 = load_settings(paths)
    assert "new pattern injected by s1" not in s2.detection_title_patterns
    assert "new pattern injected by s1" not in DEFAULT_SETTINGS["detection"]["title_patterns"]


def test_live_transcription_settings_defaults(paths: AppPaths) -> None:
    s = load_settings(paths)
    assert s.transcription_live_flush_interval_ms == 10_000
    assert s.transcription_live_max_wait_ms == 15_000


def test_open_workspace_hotkey_default(paths: AppPaths) -> None:
    s = load_settings(paths)
    assert s.hotkeys["open_workspace"] == "ctrl+alt+n"
    assert s.hotkeys["toggle_manual_recording"] == "ctrl+alt+r"
    assert s.hotkeys["toggle_pause_detection"] == "ctrl+alt+p"


def test_live_enabled_default_is_false(paths: AppPaths) -> None:
    """Phase 6 makes live transcription opt-in — default off."""
    from teams_transcriber.config import load_settings

    s = load_settings(paths)
    assert s.transcription_live_enabled is False


def test_audio_device_dict_round_trip() -> None:
    """audio_mic_device and audio_loopback_device round-trip through settings.json."""
    import tempfile
    from pathlib import Path
    from teams_transcriber.config import load_settings, save_settings
    from teams_transcriber.paths import AppPaths

    with tempfile.TemporaryDirectory() as tmp_path:
        paths = AppPaths(root=Path(tmp_path))
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
        paths = AppPaths(root=Path(tmp_path))
        paths.ensure_dirs()
        settings_path = paths.config_dir / "settings.json"
        settings_path.write_text(
            json.dumps({"audio": {"mic_device": "{old-string-id}"}}),
            encoding="utf-8",
        )
        s = load_settings(paths)
        assert s.audio_mic_device is None
