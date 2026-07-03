from __future__ import annotations

from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.main_window import MainWindow
from teams_transcriber.ui.sidebar import SidebarBucket


def test_main_window_uses_shared_chrome(qapp) -> None:
    w = MainWindow()
    assert isinstance(w, FramelessWindowMixin)
    assert w.title_bar.settings_btn is not None  # main keeps the settings cog
    # toggle_max flips style without raising
    w.toggle_max()
    w.toggle_max()


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


def test_main_window_has_chrome_shell(qapp):
    from teams_transcriber.ui.frameless import CHROME_MARGIN
    from teams_transcriber.ui.main_window import MainWindow
    w = MainWindow()
    assert w._shell_layout is not None
    assert w._shell_layout.contentsMargins().left() == CHROME_MARGIN
    assert w._outer.graphicsEffect() is not None


def test_sidebar_is_user_resizable_via_splitter(qapp):
    from PySide6.QtWidgets import QSplitter
    from teams_transcriber.ui.main_window import MainWindow
    w = MainWindow()
    assert isinstance(w.body_splitter, QSplitter)
    assert w.body_splitter.widget(0) is w.sidebar
    # Sidebar is no longer fixed-width.
    assert w.sidebar.minimumWidth() < w.sidebar.maximumWidth()


def test_main_window_minimum_allows_half_screen(qapp):
    from teams_transcriber.ui.main_window import MainWindow
    w = MainWindow()
    assert w.minimumWidth() <= 680
    assert w.minimumHeight() <= 460


def test_columns_splitter_builds_resizable_columns(qapp):
    from PySide6.QtWidgets import QSplitter, QWidget
    from teams_transcriber.ui.app import _build_columns_splitter
    left, right = QWidget(), QWidget()
    sp = _build_columns_splitter(left, right)
    assert isinstance(sp, QSplitter)
    assert sp.widget(0) is left and sp.widget(1) is right
    assert sp.childrenCollapsible() is False
