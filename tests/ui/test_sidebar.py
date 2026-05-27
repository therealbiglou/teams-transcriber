from teams_transcriber.ui.sidebar import Sidebar, SidebarBucket


def test_todos_button_emits_todos_selected(qapp):
    sb = Sidebar()
    got = []
    sb.todos_selected.connect(lambda: got.append(True))
    sb.todos_button.click()
    assert got == [True]
    assert sb._active_is_todos is True


def test_bucket_click_clears_todos_and_emits_bucket(qapp):
    sb = Sidebar()
    sb.todos_button.click()
    seen = []
    sb.bucket_selected.connect(seen.append)
    sb._buttons[SidebarBucket.MANUAL].click()
    assert seen == [SidebarBucket.MANUAL]
    assert sb._active_is_todos is False
    assert sb.active_bucket == SidebarBucket.MANUAL


def test_select_bucket_programmatically(qapp):
    sb = Sidebar()
    sb.todos_button.click()
    seen = []
    sb.bucket_selected.connect(seen.append)
    sb.select_bucket(SidebarBucket.ALL)
    assert seen == [SidebarBucket.ALL]
    assert sb._active_is_todos is False


def test_stacked_switch_contract(qapp):
    from PySide6.QtWidgets import QStackedWidget, QWidget
    stack = QStackedWidget(); page0 = QWidget(); page1 = QWidget()
    stack.addWidget(page0); stack.addWidget(page1)
    sb = Sidebar()
    sb.todos_selected.connect(lambda: stack.setCurrentIndex(1))
    sb.bucket_selected.connect(lambda _b: stack.setCurrentIndex(0))
    sb.todos_button.click()
    assert stack.currentIndex() == 1
    sb._buttons[SidebarBucket.ALL].click()
    assert stack.currentIndex() == 0


def test_master_view_go_to_summary_signal(qapp, tmp_path):
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import build_database
    from teams_transcriber.ui.master_todo_view import MasterTodoView
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    view = MasterTodoView(db)
    seen = []
    view.go_to_summary.connect(seen.append)
    view._emit_go_to_summary(42)
    assert seen == [42]
    db.close()
