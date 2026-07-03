def test_close_loop_only_returns_changed_rows():
    from teams_transcriber.storage.wrike import WrikeTaskRow
    from teams_transcriber.ui.app import _wrike_close_loop_changes
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
    from teams_transcriber.storage.wrike import WrikeTaskRow
    from teams_transcriber.ui.app import _wrike_close_loop_changes
    rows = [
        WrikeTaskRow(id=1, recording_id=10, kind="other", todo_index=0,
                     wrike_task_id="T1", wrike_folder_id="F1",
                     created_at="x", last_synced_done=False),
    ]
    # action-items-for-others are not toggleable in the app, so skip.
    assert _wrike_close_loop_changes(rows, {0: True}) == []


def test_close_loop_returns_empty_when_no_changes():
    from teams_transcriber.storage.wrike import WrikeTaskRow
    from teams_transcriber.ui.app import _wrike_close_loop_changes
    rows = [
        WrikeTaskRow(id=1, recording_id=10, kind="my", todo_index=0,
                     wrike_task_id="T1", wrike_folder_id="F1",
                     created_at="x", last_synced_done=False),
    ]
    assert _wrike_close_loop_changes(rows, {0: False}) == []


def test_master_todo_toggle_triggers_close_loop(qapp, tmp_path):
    """Master to-do view toggles must run the same Wrike close-loop as the
    summary pane's checkbox (App._on_master_todo_toggled)."""
    from types import SimpleNamespace

    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import build_database
    from teams_transcriber.ui.app import App
    from teams_transcriber.ui.master_todo_view import MasterTodoView
    from teams_transcriber.ui.sidebar import SidebarBucket

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    try:
        app = App.__new__(App)
        app.db = db
        app.master_todos = MasterTodoView(db)
        app.search = SimpleNamespace(input=SimpleNamespace(text=lambda: ""))
        app.window = SimpleNamespace(
            sidebar=SimpleNamespace(active_bucket=SidebarBucket.ALL)
        )
        app.history = SimpleNamespace(set_recordings=lambda rows: None)

        calls: list[int] = []
        app._wrike_close_loop_sync = calls.append  # type: ignore[assignment]
        app.master_todos.todo_toggled.connect(app._on_master_todo_toggled)

        app.master_todos.todo_toggled.emit(42)

        assert calls == [42]
    finally:
        db.close()


def test_build_main_content_wires_master_todo_toggle_to_close_loop():
    """Wiring regression guard: _build_main_content must connect
    todo_toggled to _on_master_todo_toggled, not a bare refresh lambda."""
    import inspect

    from teams_transcriber.ui.app import App

    source = inspect.getsource(App._build_main_content)
    assert (
        "self.master_todos.todo_toggled.connect(self._on_master_todo_toggled)"
        in source
    )
