from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QSplitter, QWidget

from teams_transcriber.ui.window_state import (
    restore_splitter_state,
    restore_window_geometry,
    save_splitter_state,
    save_window_geometry,
)


def _ini(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "state.ini"), QSettings.Format.IniFormat)


def test_geometry_roundtrip(qapp, tmp_path):
    s = _ini(tmp_path)
    w = QWidget()
    w.resize(555, 333)
    w.move(40, 50)
    save_window_geometry(w, "main", settings=s)
    s.sync()

    w2 = QWidget()
    assert restore_window_geometry(w2, "main", settings=_ini(tmp_path)) is True
    assert w2.size().width() == 555
    assert w2.size().height() == 333


def test_restore_falls_back_to_default_size(qapp, tmp_path):
    w = QWidget()
    ok = restore_window_geometry(
        w, "never-saved", default_size=(640, 480), settings=_ini(tmp_path),
    )
    assert ok is False
    assert (w.width(), w.height()) == (640, 480)


def test_splitter_roundtrip(qapp, tmp_path):
    s = _ini(tmp_path)
    sp = QSplitter()
    sp.addWidget(QWidget())
    sp.addWidget(QWidget())
    sp.resize(1000, 400)
    sp.setSizes([300, 700])
    save_splitter_state(sp, "cols", settings=s)
    s.sync()

    sp2 = QSplitter()
    sp2.addWidget(QWidget())
    sp2.addWidget(QWidget())
    sp2.resize(1000, 400)
    assert restore_splitter_state(sp2, "cols", settings=_ini(tmp_path)) is True
    assert sp2.sizes()[0] < sp2.sizes()[1]
