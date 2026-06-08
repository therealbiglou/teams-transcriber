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


def test_picker_accept_skips_hidden_current_item(qapp):
    from teams_transcriber.ui.wrike_folder_picker import WrikeFolderPicker
    dlg = WrikeFolderPicker(
        folders=[
            {"id": "F1", "title": "Inbox"},
            {"id": "F2", "title": "Meetings"},
        ],
        recent_folder_ids=[],
    )
    dlg._list.setCurrentRow(0)
    dlg._search.setText("meet")   # hides the Inbox row that was selected
    dlg._on_accept()
    # selected_folder_id must come from a visible row (the Meetings row), not the hidden Inbox.
    assert dlg.selected_folder_id == "F2"
