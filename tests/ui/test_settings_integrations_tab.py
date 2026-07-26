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


def test_integrations_tab_present_with_token_and_project_export(qapp, paths):
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    titles = [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())]
    assert "Integrations" in titles
    assert dlg.wrike_token_input is not None
    assert dlg.wrike_project_export_cb is not None
    assert dlg.wrike_project_export_cb.isChecked() is False


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


def test_wrike_test_recovers_when_client_constructor_raises(qapp, qtbot, paths, monkeypatch) -> None:
    from teams_transcriber.integrations import wrike_client

    class _BoomClient:
        def __init__(self, *, token, **_):
            raise RuntimeError("proxy exploded")

    monkeypatch.setattr(wrike_client, "WrikeClient", _BoomClient)

    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg.wrike_token_input.setText("tok")
    dlg._wrike_test_connection()
    qtbot.waitUntil(lambda: dlg._wrike_test_btn.isEnabled(), timeout=3000)
    assert "✗" in dlg.wrike_status_label.text()
    assert "proxy exploded" in dlg.wrike_status_label.text()


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


def test_project_export_toggle_and_destination_persist(qapp, paths):
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)

    assert dlg.wrike_project_export_cb.isChecked() is False
    assert dlg.wrike_dest_label.text() == "No destination chosen"

    dlg.wrike_project_export_cb.setChecked(True)
    dlg._chosen_parent = ("f1", "Team / Meetings")
    dlg._on_accept()

    reloaded = load_settings(paths)
    integ = reloaded._raw["integrations"]
    assert integ["wrike_project_export_enabled"] is True
    assert integ["wrike_parent_id"] == "f1"
    assert integ["wrike_parent_label"] == "Team / Meetings"


def test_old_auto_send_todos_checkbox_is_gone(qapp, paths):
    import inspect

    from teams_transcriber.ui import settings_dialog as sd

    src = inspect.getsource(sd.SettingsDialog._build_integrations_tab)
    assert "wrike_enable_cb" not in src        # replaced by project-export
    assert "wrike_project_export_cb" in src


def test_group_folders_by_space_uses_child_ids_when_present():
    from teams_transcriber.ui.settings_dialog import _group_folders_by_space

    spaces = [
        {"id": "sp1", "title": "Team", "childIds": ["f1"]},
        {"id": "sp2", "title": "Personal", "childIds": ["f2"]},
    ]
    folders = [{"id": "f1", "title": "Meetings"}, {"id": "f2", "title": "Notes"}]

    grouped = _group_folders_by_space(spaces, folders)

    assert grouped == {"sp1": [{"id": "f1", "title": "Meetings"}],
                       "sp2": [{"id": "f2", "title": "Notes"}]}


def test_group_folders_by_space_falls_back_when_child_ids_absent():
    from teams_transcriber.ui.settings_dialog import _group_folders_by_space

    spaces = [{"id": "sp1", "title": "Team"}]  # no childIds
    folders = [{"id": "f1", "title": "Meetings"}, {"id": "f2", "title": "Notes"}]

    grouped = _group_folders_by_space(spaces, folders)

    assert grouped == {"sp1": folders}


def test_group_folders_by_space_mixed_child_ids_per_space():
    # The case the old global fallback broke: one space with childIds, one
    # without. The childId space gets its subset; the childless space gets
    # all folders (not an empty list).
    from teams_transcriber.ui.settings_dialog import _group_folders_by_space

    spaces = [
        {"id": "sp1", "title": "Team", "childIds": ["f1"]},
        {"id": "sp2", "title": "Personal"},  # no childIds
    ]
    folders = [{"id": "f1", "title": "Meetings"}, {"id": "f2", "title": "Notes"}]

    grouped = _group_folders_by_space(spaces, folders)

    assert grouped["sp1"] == [{"id": "f1", "title": "Meetings"}]
    assert grouped["sp2"] == folders
