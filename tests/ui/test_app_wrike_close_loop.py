def test_master_todo_toggle_refreshes_history(qapp, tmp_path):
    """Master to-do view toggles refresh the history chip."""
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

        refresh_calls: list[int] = []
        app._refresh_history = lambda query=None: refresh_calls.append(1)
        app.master_todos.todo_toggled.connect(app._on_master_todo_toggled)

        app.master_todos.todo_toggled.emit(42)

        assert refresh_calls == [1]
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
