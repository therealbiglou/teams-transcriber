from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel

from teams_transcriber.ui.labels import ElidedLabel, make_selectable, make_todo_row, make_wrapping


def test_make_selectable_sets_flags(qapp):
    lbl = make_selectable(QLabel("x"))
    flags = lbl.textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
    assert flags & Qt.TextInteractionFlag.TextSelectableByKeyboard


def test_make_wrapping_applies_all_three_guards(qapp):
    from PySide6.QtWidgets import QSizePolicy
    lbl = make_wrapping(QLabel("x"))
    assert lbl.wordWrap() is True
    assert lbl.minimumWidth() == 0
    assert lbl.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored


def test_make_todo_row_wraps_and_selects(qapp):
    row = make_todo_row("task text", checked=True, on_toggle=lambda _c: None)
    cb = row.findChild(QCheckBox)
    lbl = row.findChild(QLabel)
    assert cb.isChecked() is True
    assert cb.text() == ""          # text lives in the wrapping label, not the checkbox
    assert lbl.wordWrap() is True
    assert lbl.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse


def test_todo_row_toggle_fires_callback(qapp):
    calls: list[bool] = []
    row = make_todo_row("t", checked=False, on_toggle=calls.append)
    row.findChild(QCheckBox).setChecked(True)
    assert calls == [True]


def test_elided_label_elides_and_tooltips(qapp):
    lbl = ElidedLabel()
    lbl.setFixedWidth(60)
    long = "A very long recording title that cannot possibly fit in sixty pixels"
    lbl.set_full_text(long)
    assert lbl.toolTip() == long
    assert lbl.text() != long
    assert lbl.text().endswith("…")
