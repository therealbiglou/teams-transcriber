from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QFrame, QWidget
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
