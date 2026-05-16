from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.config import load_settings
from teams_transcriber.paths import AppPaths
from teams_transcriber.ui.first_run_wizard import FirstRunWizard


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT")
    p.ensure_dirs()
    return p


@pytest.fixture(autouse=True)
def _isolate_keyring_and_registry(monkeypatch):
    """Keep wizard tests from touching real keyring and HKCU Run key."""
    monkeypatch.setattr("keyring.set_password", lambda *a, **kw: None)
    monkeypatch.setattr(
        "teams_transcriber.autolaunch.enable", lambda *a, **kw: True,
    )
    monkeypatch.setattr(
        "teams_transcriber.autolaunch.disable", lambda: True,
    )


def test_finish_writes_marker_file(qapp, qtbot, paths: AppPaths) -> None:
    settings = load_settings(paths)
    wizard = FirstRunWizard(
        settings=settings, paths=paths, model_downloader=lambda cb: None,
    )
    wizard.auto_launch_cb.setChecked(False)
    wizard._finish()
    assert paths.first_run_marker_path.exists()


def test_finish_persists_api_key_when_provided(
    qapp, qtbot, paths: AppPaths, monkeypatch,
) -> None:
    settings = load_settings(paths)
    saved: dict[str, str] = {}
    monkeypatch.setattr(
        "keyring.set_password",
        lambda svc, usr, pw: saved.setdefault(usr, pw),
    )
    wizard = FirstRunWizard(
        settings=settings, paths=paths, model_downloader=lambda cb: None,
    )
    wizard.api_key_input.setText("sk-test-fake")
    wizard.auto_launch_cb.setChecked(False)
    wizard._finish()
    assert saved.get("anthropic_api_key") == "sk-test-fake"


def test_finish_skips_keyring_when_api_key_blank(
    qapp, qtbot, paths: AppPaths, monkeypatch,
) -> None:
    settings = load_settings(paths)
    calls: list[tuple] = []
    monkeypatch.setattr("keyring.set_password", lambda *a, **kw: calls.append(a))
    wizard = FirstRunWizard(
        settings=settings, paths=paths, model_downloader=lambda cb: None,
    )
    wizard.api_key_input.setText("")
    wizard.auto_launch_cb.setChecked(False)
    wizard._finish()
    assert calls == []


def test_finish_syncs_autolaunch(
    qapp, qtbot, paths: AppPaths, monkeypatch,
) -> None:
    settings = load_settings(paths)
    calls: list[str] = []
    monkeypatch.setattr(
        "teams_transcriber.autolaunch.enable",
        lambda *a, **kw: calls.append("enable") or True,
    )
    monkeypatch.setattr(
        "teams_transcriber.autolaunch.disable",
        lambda: calls.append("disable") or True,
    )
    wizard = FirstRunWizard(
        settings=settings, paths=paths, model_downloader=lambda cb: None,
    )
    wizard.api_key_input.setText("")
    wizard.auto_launch_cb.setChecked(True)
    wizard._finish()
    assert calls == ["enable"]
