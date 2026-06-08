def test_close_loop_only_returns_changed_rows():
    from teams_transcriber.ui.app import _wrike_close_loop_changes
    from teams_transcriber.storage.wrike import WrikeTaskRow
    rows = [
        WrikeTaskRow(id=1, recording_id=10, kind="my", todo_index=0,
                     wrike_task_id="T1", wrike_folder_id="F1",
                     created_at="x", last_synced_done=False),
        WrikeTaskRow(id=2, recording_id=10, kind="my", todo_index=1,
                     wrike_task_id="T2", wrike_folder_id="F1",
                     created_at="x", last_synced_done=True),
    ]
    # idx 0 toggled True (was False) -> change; idx 1 already True -> no change.
    changes = _wrike_close_loop_changes(rows, {0: True, 1: True})
    assert len(changes) == 1
    row, new_done = changes[0]
    assert row.wrike_task_id == "T1" and new_done is True


def test_close_loop_ignores_other_kind():
    from teams_transcriber.ui.app import _wrike_close_loop_changes
    from teams_transcriber.storage.wrike import WrikeTaskRow
    rows = [
        WrikeTaskRow(id=1, recording_id=10, kind="other", todo_index=0,
                     wrike_task_id="T1", wrike_folder_id="F1",
                     created_at="x", last_synced_done=False),
    ]
    # action-items-for-others are not toggleable in the app, so skip.
    assert _wrike_close_loop_changes(rows, {0: True}) == []


def test_close_loop_returns_empty_when_no_changes():
    from teams_transcriber.ui.app import _wrike_close_loop_changes
    from teams_transcriber.storage.wrike import WrikeTaskRow
    rows = [
        WrikeTaskRow(id=1, recording_id=10, kind="my", todo_index=0,
                     wrike_task_id="T1", wrike_folder_id="F1",
                     created_at="x", last_synced_done=False),
    ]
    assert _wrike_close_loop_changes(rows, {0: False}) == []
