from teams_transcriber.ui.wrike_folder_picker import WrikeFolderPicker


def _folders():
    return [
        {"id": "F1", "title": "Inbox"},
        {"id": "F2", "title": "Meetings"},
        {"id": "F3", "title": "Personal"},
    ]


def test_picker_lists_recent_first_then_rest(qapp):
    dlg = WrikeFolderPicker(folders=_folders(), recent_folder_ids=["F2"])
    items = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    # "Meetings ★" should be first (recent), then "Inbox" and "Personal".
    assert items[0].startswith("Meetings")
    assert "Inbox" in items[1] or "Inbox" in items[2]


def test_picker_search_filters_visible_rows(qapp):
    dlg = WrikeFolderPicker(folders=_folders(), recent_folder_ids=[])
    dlg._search.setText("meet")
    visible_texts = [
        dlg._list.item(i).text()
        for i in range(dlg._list.count())
        if not dlg._list.item(i).isHidden()
    ]
    assert visible_texts
    assert all("meet" in t.lower() for t in visible_texts)


def test_picker_returns_selected_folder_id(qapp):
    dlg = WrikeFolderPicker(folders=_folders(), recent_folder_ids=[])
    dlg._list.setCurrentRow(0)
    dlg._on_accept()
    assert dlg.selected_folder_id in {"F1", "F2", "F3"}
