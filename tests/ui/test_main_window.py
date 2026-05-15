from __future__ import annotations

from teams_transcriber.ui.main_window import MainWindow
from teams_transcriber.ui.sidebar import SidebarBucket


def test_main_window_constructs(qapp, qtbot) -> None:
    win = MainWindow()
    assert win.sidebar.active_bucket == SidebarBucket.ALL
    assert win.title_bar is not None


def test_sidebar_selection_emits_signal(qapp, qtbot) -> None:
    win = MainWindow()
    received: list[SidebarBucket] = []
    win.sidebar.bucket_selected.connect(received.append)
    win.sidebar._buttons[SidebarBucket.TODAY].click()
    assert received == [SidebarBucket.TODAY]
    assert win.sidebar.active_bucket == SidebarBucket.TODAY


def test_set_content_replaces_widget(qapp, qtbot) -> None:
    from PySide6.QtWidgets import QLabel
    win = MainWindow()
    label_one = QLabel("first")
    win.set_content(label_one)
    label_two = QLabel("second")
    win.set_content(label_two)
    # After replacement, only label_two should be in the content layout.
    assert win._content_layout.count() == 1
    assert win._content_layout.itemAt(0).widget() is label_two
