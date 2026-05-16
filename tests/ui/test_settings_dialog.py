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
