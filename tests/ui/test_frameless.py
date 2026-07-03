from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from teams_transcriber.ui.frameless import FramelessWindowMixin


class _Win(FramelessWindowMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.resize(400, 300)
        frame = QFrame(self)
        self._init_frameless(frame)


def test_edge_detection_corners_and_edges(qapp):
    w = _Win()
    w.resize(400, 300)
    assert w._edge_at(QPoint(2, 2)) == (Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
    assert w._edge_at(QPoint(398, 298)) == (Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
    assert w._edge_at(QPoint(2, 150)) == Qt.Edge.LeftEdge
    assert w._edge_at(QPoint(200, 298)) == Qt.Edge.BottomEdge
    # PySide6 6.11: empty Qt.Edges() is not int()-convertible; use .value.
    assert w._edge_at(QPoint(200, 150)).value == 0   # interior


def test_resizable_false_disables_edges(qapp):
    class _Fixed(FramelessWindowMixin, QWidget):
        def __init__(self):
            super().__init__()
            self.resize(400, 300)
            self._init_frameless(QFrame(self), resizable=False)
    w = _Fixed()
    assert w._edge_at(QPoint(2, 2)).value == 0


def test_titlebar_builds_only_requested_controls(qapp):
    from teams_transcriber.ui.title_bar import TitleBar
    tb = TitleBar(title="X", controls=("close",))
    assert tb.title_label.text() == "X"
    assert tb.close_btn is not None
    assert tb.minimize_btn is None
    assert tb.maximize_btn is None
    assert tb.settings_btn is None


def test_titlebar_full_controls(qapp):
    from teams_transcriber.ui.title_bar import TitleBar
    tb = TitleBar(title="Main", controls=("settings", "min", "max", "close"))
    assert tb.settings_btn is not None
    assert tb.minimize_btn is not None
    assert tb.maximize_btn is not None
    assert tb.close_btn is not None


class _ChromeWin(FramelessWindowMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.resize(400, 300)
        shell = QVBoxLayout(self)
        frame = QFrame()
        shell.addWidget(frame)
        self._init_frameless(frame, shell_layout=shell)


def test_shell_layout_gets_chrome_margins(qapp):
    from teams_transcriber.ui.frameless import CHROME_MARGIN
    w = _ChromeWin()
    m = w._shell_layout.contentsMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (CHROME_MARGIN,) * 4


def test_chrome_margins_collapse_when_maximized(qapp):
    w = _ChromeWin()
    w.showMaximized()
    w._apply_chrome()
    m = w._shell_layout.contentsMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (0, 0, 0, 0)
    w.showNormal()
    w._apply_chrome()
    assert w._shell_layout.contentsMargins().left() > 0


def test_outer_frame_has_window_shadow(qapp):
    from PySide6.QtWidgets import QGraphicsDropShadowEffect
    w = _ChromeWin()
    assert isinstance(w._outer.graphicsEffect(), QGraphicsDropShadowEffect)


def test_resize_band_covers_chrome_margin(qapp):
    from teams_transcriber.ui.frameless import CHROME_MARGIN
    w = _ChromeWin()
    w.resize(400, 300)
    # Anywhere in the transparent margin band is a resize edge.
    assert w._edge_at(QPoint(CHROME_MARGIN - 2, 150)) == Qt.Edge.LeftEdge
    assert w._edge_at(QPoint(200, 150)).value == 0


def test_legacy_init_without_shell_layout_keeps_old_band(qapp):
    w = _Win()  # existing helper: no shell_layout
    assert w._edge_at(QPoint(2, 150)) == Qt.Edge.LeftEdge
    assert w._edge_at(QPoint(10, 150)).value == 0
    assert w._outer.graphicsEffect() is None


def test_titlebar_set_window_active_dims_title(qapp):
    from teams_transcriber.ui.title_bar import TitleBar
    tb = TitleBar(title="X", controls=("close",))
    tb.set_window_active(False)
    assert "color" in tb.title_label.styleSheet()
    tb.set_window_active(True)
    assert tb.title_label.styleSheet() == ""


def test_titlebar_drag_moves_window_via_fallback(qapp):
    """The window is never shown, so windowHandle() is None (and even when
    shown, offscreen startSystemMove() returns False) — either way the
    manual move() fallback must move the window."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    from teams_transcriber.ui.title_bar import TitleBar

    win = QWidget()
    win.resize(300, 200)
    tb = TitleBar(win, title="T", controls=("close",))
    win.move(100, 100)

    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(50, 10), QPointF(150, 110),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tb.mousePressEvent(press)
    move = QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(70, 20), QPointF(170, 120),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tb.mouseMoveEvent(move)
    assert win.pos().x() == 120
    assert win.pos().y() == 110


def test_titlebar_drag_restores_maximized_window(qapp):
    """Dragging a maximized window restores it and re-anchors it so the
    cursor stays at the same relative x over the (now normal-size) window."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QVBoxLayout

    from teams_transcriber.ui.title_bar import TitleBar

    win = _Win()  # FramelessWindowMixin host with toggle_max
    win.resize(400, 300)
    tb = TitleBar(win, title="T", controls=("close",))
    layout = QVBoxLayout(win)
    layout.addWidget(tb)
    layout.addStretch(1)
    win.show()
    qapp.processEvents()
    win.showMaximized()
    qapp.processEvents()
    assert win.isMaximized()

    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(50, 10), QPointF(50, 10),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tb.mousePressEvent(press)

    # Title-bar width at the time of the move (still maximized) — the same
    # value the handler uses to compute the relative cursor anchor.
    tb_width_at_move = tb.width()
    move_x, move_y = 60.0, 15.0
    move = QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(move_x, move_y),
        QPointF(move_x, move_y),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tb.mouseMoveEvent(move)

    assert not win.isMaximized()
    # Cursor-anchor math: the restored window is positioned so the cursor's
    # global x sits at the same relative x it had over the maximized title
    # bar. Deterministic offscreen, so exact integer equality holds.
    rel_x_expected = move_x / max(1, tb_width_at_move)
    assert win.pos().x() == int(move_x - win.width() * rel_x_expected)
    assert win.pos().y() == int(move_y) - tb.height() // 2
