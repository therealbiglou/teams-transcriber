from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.config import load_settings
from teams_transcriber.paths import AppPaths
from teams_transcriber.ui.settings_dialog import SettingsDialog


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT")
    p.ensure_dirs()
    return p


def test_integrations_tab_present_with_token_and_enable(qapp, paths):
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    titles = [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())]
    assert "Integrations" in titles
    assert dlg.wrike_token_input is not None
    assert dlg.wrike_enable_cb is not None
    assert dlg.wrike_enable_cb.isChecked() is False


def test_test_connection_updates_label_on_success(qapp, qtbot, paths, monkeypatch):
    from teams_transcriber.integrations import wrike_client
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg.wrike_token_input.setText("tok")

    class _FakeClient:
        def __init__(self, *, token, **_):
            pass

        def test_connection(self):
            return {"id": "U1", "firstName": "Brian"}

        def close(self):
            pass

    monkeypatch.setattr(wrike_client, "WrikeClient", _FakeClient)
    dlg._wrike_test_connection()
    qtbot.waitUntil(lambda: "Checking" not in dlg.wrike_status_label.text(), timeout=3000)
    assert "Brian" in dlg.wrike_status_label.text()


def test_test_connection_shows_error_on_auth_failure(qapp, qtbot, paths, monkeypatch):
    from teams_transcriber.integrations import wrike_client
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg.wrike_token_input.setText("tok")

    class _FakeClient:
        def __init__(self, *, token, **_):
            pass

        def test_connection(self):
            from teams_transcriber.integrations.wrike_client import WrikeAuthError
            raise WrikeAuthError("bad token")

        def close(self):
            pass

    monkeypatch.setattr(wrike_client, "WrikeClient", _FakeClient)
    dlg._wrike_test_connection()
    qtbot.waitUntil(lambda: "Checking" not in dlg.wrike_status_label.text(), timeout=3000)
    txt = dlg.wrike_status_label.text().lower()
    assert "bad token" in txt or "failed" in txt or "✗" in dlg.wrike_status_label.text()


def test_wrike_test_disables_button_while_checking(qapp, qtbot, paths, monkeypatch) -> None:
    import threading

    from teams_transcriber.integrations import wrike_client

    gate = threading.Event()

    class _SlowClient:
        def __init__(self, *, token, **_):
            pass

        def test_connection(self):
            gate.wait(timeout=5)
            return {"firstName": "A", "lastName": "B"}

        def close(self):
            pass

    monkeypatch.setattr(wrike_client, "WrikeClient", _SlowClient)

    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg.wrike_token_input.setText("tok")
    dlg._wrike_test_connection()
    assert not dlg._wrike_test_btn.isEnabled()
    gate.set()
    qtbot.waitUntil(lambda: dlg._wrike_test_btn.isEnabled(), timeout=3000)
    assert "Connected as A B" in dlg.wrike_status_label.text()
