from __future__ import annotations

from teams_transcriber.ui.wrike_destination_picker import WrikeDestinationPicker

_SPACES = [{"id": "sp1", "title": "Team"}, {"id": "sp2", "title": "Personal"}]
_FOLDERS = {"sp1": [{"id": "f1", "title": "Meetings"}], "sp2": []}


def test_selecting_space_root_returns_space(qapp):
    dlg = WrikeDestinationPicker(spaces=_SPACES, folders_by_space=_FOLDERS)
    dlg._select_space("sp1")
    dlg._select_target(None)            # (space root)
    assert dlg.selected == ("sp1", "Team")


def test_selecting_folder_returns_folder_with_path_label(qapp):
    dlg = WrikeDestinationPicker(spaces=_SPACES, folders_by_space=_FOLDERS)
    dlg._select_space("sp1")
    dlg._select_target("f1")
    assert dlg.selected == ("f1", "Team / Meetings")
