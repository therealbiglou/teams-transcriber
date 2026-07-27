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


def test_build_project_type_choices_filters_and_labels_with_space_names():
    from teams_transcriber.ui.settings_dialog import _build_project_type_choices

    spaces = [
        {"id": "sp1", "title": "Brian's Sandbox"},
        {"id": "sp2", "title": "Ops"},
    ]
    custom_item_types = [
        {"id": "t1", "title": "Meeting", "relatedType": "Project", "spaceId": "sp1",
         "isDeleted": False},
        {"id": "t2", "title": "Project Phase", "relatedType": "Project", "spaceId": "sp2",
         "isDeleted": False},
        # Not a Project type -> excluded.
        {"id": "t3", "title": "User Story", "relatedType": "Task", "spaceId": "sp1",
         "isDeleted": False},
        # Deleted -> excluded even though relatedType matches.
        {"id": "t4", "title": "Old Type", "relatedType": "Project", "spaceId": "sp1",
         "isDeleted": True},
        # Unknown space -> falls back to the bare title.
        {"id": "t5", "title": "Mystery", "relatedType": "Project", "spaceId": "sp-unknown",
         "isDeleted": False},
    ]

    choices = _build_project_type_choices(custom_item_types, spaces)

    assert choices == [
        ("t1", "Meeting — Brian's Sandbox"),
        ("t5", "Mystery"),
        ("t2", "Project Phase — Ops"),
    ]


def test_project_type_combo_defaults_to_standard_project(qapp, paths):
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    assert dlg.wrike_item_type_combo.count() == 1
    assert dlg.wrike_item_type_combo.currentData() is None
    assert dlg.wrike_item_type_combo.currentText() == "(Standard project)"


def test_populate_wrike_item_type_combo_selects_fetched_match(qapp, paths):
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg._custom_item_type_id = "t1"
    dlg._custom_item_type_label = "Meeting — Brian's Sandbox"

    dlg._populate_wrike_item_type_combo([
        ("t1", "Meeting — Brian's Sandbox"),
        ("t2", "Project Phase — Ops"),
    ])

    assert dlg.wrike_item_type_combo.currentData() == "t1"
    assert dlg.wrike_item_type_combo.currentText() == "Meeting — Brian's Sandbox"
    assert dlg.wrike_item_type_combo.count() == 3  # standard + 2 fetched


def test_populate_wrike_item_type_combo_keeps_saved_choice_when_absent_from_fetch(qapp, paths):
    """If the saved custom-item-type id isn't among the freshly-fetched
    types (deleted, different account state, ...), the user's prior choice
    must not be silently dropped -- it stays selected via its saved label."""
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg._custom_item_type_id = "gone"
    dlg._custom_item_type_label = "Vanished Type"

    dlg._populate_wrike_item_type_combo([
        ("t1", "Meeting — Brian's Sandbox"),
    ])

    assert dlg.wrike_item_type_combo.currentData() == "gone"
    assert dlg.wrike_item_type_combo.currentText() == "Vanished Type"
    assert dlg.wrike_item_type_combo.count() == 3  # standard + fetched + saved-but-missing


def test_project_type_setting_persists_round_trip(qapp, paths):
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)
    dlg._populate_wrike_item_type_combo([("t1", "Meeting — Brian's Sandbox")])
    idx = dlg.wrike_item_type_combo.findData("t1")
    dlg.wrike_item_type_combo.setCurrentIndex(idx)

    dlg._on_accept()

    reloaded = load_settings(paths)
    integ = reloaded._raw["integrations"]
    assert integ["wrike_custom_item_type_id"] == "t1"
    assert integ["wrike_custom_item_type_label"] == "Meeting — Brian's Sandbox"

    dlg2 = SettingsDialog(reloaded, paths)
    assert dlg2.wrike_item_type_combo.currentData() == "t1"
    assert dlg2.wrike_item_type_combo.currentText() == "Meeting — Brian's Sandbox"


def test_project_type_setting_defaults_to_none_when_left_standard(qapp, paths):
    settings = load_settings(paths)
    dlg = SettingsDialog(settings, paths)

    dlg._on_accept()

    reloaded = load_settings(paths)
    integ = reloaded._raw["integrations"]
    assert integ["wrike_custom_item_type_id"] is None
    assert integ["wrike_custom_item_type_label"] is None


def test_group_folders_by_space_uses_real_space_child_ids():
    from teams_transcriber.ui.settings_dialog import _group_folders_by_space

    space_child_ids = {"sp1": ["f1"], "sp2": ["f2"]}
    folders = [{"id": "f1", "title": "Meetings"}, {"id": "f2", "title": "Notes"}]

    grouped = _group_folders_by_space(space_child_ids, folders)

    assert grouped == {"sp1": [{"id": "f1", "title": "Meetings"}],
                       "sp2": [{"id": "f2", "title": "Notes"}]}


def test_group_folders_by_space_with_no_children_yields_empty_list():
    # A space with no children must yield [], never "every folder in the
    # account" — that global fallback is exactly the bug this replaces.
    from teams_transcriber.ui.settings_dialog import _group_folders_by_space

    space_child_ids = {"sp1": []}
    folders = [{"id": "f1", "title": "Meetings"}, {"id": "f2", "title": "Notes"}]

    grouped = _group_folders_by_space(space_child_ids, folders)

    assert grouped == {"sp1": []}
    assert grouped["sp1"] != folders


def test_folder_from_another_space_never_appears_under_a_space():
    # The regression, stated explicitly: space A's subtree has a folder
    # titled "Meetings"; space B's subtree has a *different* folder also
    # titled "Meetings". A's list must contain only A's folder id, and B's
    # folder must never leak into A's list (this is what the old
    # childIds-on-/spaces fallback did — it showed every folder in the
    # account under every space).
    from teams_transcriber.ui.settings_dialog import _group_folders_by_space

    space_child_ids = {
        "spA": ["fA_meetings"],
        "spB": ["fB_admin"],
    }
    folders = [
        {"id": "fA_meetings", "title": "Meetings"},
        {"id": "fB_admin", "title": "Admin", "childIds": ["fB_meetings"]},
        {"id": "fB_meetings", "title": "Meetings"},
    ]

    grouped = _group_folders_by_space(space_child_ids, folders)

    a_ids = {f["id"] for f in grouped["spA"]}
    assert a_ids == {"fA_meetings"}
    assert "fB_meetings" not in a_ids
    assert "fB_admin" not in a_ids

    b_ids = {f["id"] for f in grouped["spB"]}
    assert b_ids == {"fB_admin", "fB_meetings"}


def test_group_folders_by_space_nested_folder_gets_path_label():
    from teams_transcriber.ui.settings_dialog import _group_folders_by_space

    space_child_ids = {"sp1": ["admin"]}
    folders = [
        {"id": "admin", "title": "Admin", "childIds": ["meetings"]},
        {"id": "meetings", "title": "Meetings"},
    ]

    grouped = _group_folders_by_space(space_child_ids, folders)

    titles = {f["id"]: f["title"] for f in grouped["sp1"]}
    assert titles == {"admin": "Admin", "meetings": "Admin / Meetings"}


def test_group_folders_by_space_skips_unknown_child_ids():
    from teams_transcriber.ui.settings_dialog import _group_folders_by_space

    space_child_ids = {"sp1": ["f1", "ghost"]}
    folders = [{"id": "f1", "title": "Meetings"}]

    grouped = _group_folders_by_space(space_child_ids, folders)

    assert grouped == {"sp1": [{"id": "f1", "title": "Meetings"}]}


def test_group_folders_by_space_missing_child_ids_degrades_to_top_level():
    from teams_transcriber.ui.settings_dialog import _group_folders_by_space

    space_child_ids = {"sp1": ["f1"]}
    # Folder present but with no childIds key at all — must not raise, and
    # must not attempt to descend further.
    folders = [{"id": "f1", "title": "Meetings"}]

    grouped = _group_folders_by_space(space_child_ids, folders)

    assert grouped == {"sp1": [{"id": "f1", "title": "Meetings"}]}


def test_group_folders_by_space_sorted_by_path():
    from teams_transcriber.ui.settings_dialog import _group_folders_by_space

    space_child_ids = {"sp1": ["z", "a"]}
    folders = [{"id": "z", "title": "Zulu"}, {"id": "a", "title": "Alpha"}]

    grouped = _group_folders_by_space(space_child_ids, folders)

    assert [f["title"] for f in grouped["sp1"]] == ["Alpha", "Zulu"]
