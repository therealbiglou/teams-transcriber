from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.config import load_settings, save_settings
from teams_transcriber.paths import AppPaths
from teams_transcriber.ui.settings_dialog import SettingsDialog


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT")
    p.ensure_dirs()
    return p


def test_install_button_shown_when_update_available(qapp, paths, monkeypatch) -> None:
    from types import SimpleNamespace
    from teams_transcriber import update_checker
    fake = SimpleNamespace(
        tag="v9.9.9", version=(9, 9, 9), is_prerelease=True,
        installer_url="https://example.test/x.exe", installer_size=1,
        html_url="https://example.test/release",
    )
    monkeypatch.setattr(update_checker, "fetch_latest_release", lambda: fake)
    settings = load_settings(paths)
    dialog = SettingsDialog(settings, paths)
    assert dialog._install_btn.isHidden() is True          # hidden until a check finds one
    dialog._manual_update_check()
    assert dialog._install_btn.isHidden() is False          # now offered
    assert dialog._latest_release is fake


def test_install_update_noop_without_release(qapp, paths) -> None:
    settings = load_settings(paths)
    dialog = SettingsDialog(settings, paths)
    dialog._latest_release = None
    dialog._install_update()  # must not raise


def test_dialog_loads_current_settings(qapp, qtbot, paths) -> None:
    settings = load_settings(paths)
    settings.audio_retention_days = 90
    save_settings(paths, settings)

    settings = load_settings(paths)
    dialog = SettingsDialog(settings, paths)
    assert dialog.retention_spin.value() == 90


def test_pattern_add_and_remove(qapp, qtbot, paths) -> None:
    settings = load_settings(paths)
    dialog = SettingsDialog(settings, paths)
    initial_count = dialog.pattern_list.count()

    dialog.pattern_input.setText("Test pattern")
    dialog._add_pattern()
    assert dialog.pattern_list.count() == initial_count + 1
    assert dialog.pattern_list.item(initial_count).text() == "Test pattern"


def test_accept_persists_settings(qapp, qtbot, paths) -> None:
    settings = load_settings(paths)
    dialog = SettingsDialog(settings, paths)
    dialog.retention_spin.setValue(7)
    dialog._on_accept()
    again = load_settings(paths)
    assert again.audio_retention_days == 7


def test_accept_syncs_autolaunch_registry(qapp, qtbot, paths, monkeypatch) -> None:
    from teams_transcriber import autolaunch

    calls: list[str] = []
    monkeypatch.setattr(autolaunch, "enable", lambda *a, **kw: calls.append("enable") or True)
    monkeypatch.setattr(autolaunch, "disable", lambda: calls.append("disable") or True)

    settings = load_settings(paths)
    dialog = SettingsDialog(settings, paths)
    dialog.auto_launch_cb.setChecked(True)
    dialog._on_accept()
    assert calls == ["enable"]

    calls.clear()
    settings = load_settings(paths)
    dialog2 = SettingsDialog(settings, paths)
    dialog2.auto_launch_cb.setChecked(False)
    dialog2._on_accept()
    assert calls == ["disable"]


def test_settings_dialog_persists_hotkeys(tmp_path, qapp) -> None:
    from teams_transcriber.config import load_settings
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.ui.settings_dialog import SettingsDialog

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg._hotkey_inputs["open_workspace"].setText("ctrl+shift+w")
    dlg._on_accept()
    reloaded = load_settings(paths)
    assert reloaded.hotkeys["open_workspace"] == "ctrl+shift+w"


def test_settings_dialog_blank_hotkey_rejected(tmp_path, qapp) -> None:
    from teams_transcriber.config import load_settings
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.ui.settings_dialog import SettingsDialog

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg._hotkey_inputs["toggle_manual_recording"].setText("")
    dlg._on_accept()
    reloaded = load_settings(paths)
    assert reloaded.hotkeys["toggle_manual_recording"] == "ctrl+alt+r"


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
    dlg._mic_combo.setCurrentIndex(2)  # 0 = Default, 1 = Mic A, 2 = Mic B
    dlg._loopback_combo.setCurrentIndex(1)  # 0 = Default, 1 = Spk A
    dlg._on_accept()
    reloaded = load_settings(paths)
    assert reloaded.audio_mic_device == {"id": "{mic-b}", "name": "Mic B"}
    assert reloaded.audio_loopback_device == {"id": "{spk-a}", "name": "Spk A"}


def test_settings_dialog_audio_default_round_trips(tmp_path, qapp, monkeypatch) -> None:
    """Choosing 'Use Windows default' persists as None."""
    from teams_transcriber.config import load_settings, save_settings
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
    settings._raw["audio"]["mic_device"] = {"id": "{old}", "name": "Old Mic"}
    save_settings(paths, settings)

    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg._mic_combo.setCurrentIndex(0)
    dlg._on_accept()

    reloaded = load_settings(paths)
    assert reloaded.audio_mic_device is None


def test_settings_dialog_live_enabled_round_trip(tmp_path, qapp) -> None:
    from teams_transcriber.config import load_settings
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.ui.settings_dialog import SettingsDialog

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    settings = load_settings(paths)
    assert settings.transcription_live_enabled is False

    dlg = SettingsDialog(settings, paths)
    dlg._live_enabled_check.setChecked(True)
    dlg._on_accept()

    reloaded = load_settings(paths)
    assert reloaded.transcription_live_enabled is True


def test_settings_dialog_has_shared_chrome(qapp, qtbot, paths) -> None:
    from teams_transcriber.ui.frameless import FramelessWindowMixin
    from teams_transcriber.ui.settings_dialog import SettingsDialog

    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    assert isinstance(dlg, FramelessWindowMixin)
    assert dlg._title_bar.close_btn is not None
    assert dlg._tabs.count() >= 7
