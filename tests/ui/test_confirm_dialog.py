from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton

from teams_transcriber.ui.confirm_dialog import ConfirmDialog


def test_cancel_label_none_hides_cancel_button(qapp):
    dlg = ConfirmDialog(title="T", body="B", confirm_label="OK", cancel_label=None)
    texts = [b.text() for b in dlg.findChildren(QPushButton)]
    assert texts == ["OK"]


def test_default_still_has_both_buttons(qapp):
    dlg = ConfirmDialog(title="T", body="B")
    texts = [b.text() for b in dlg.findChildren(QPushButton)]
    assert texts == ["Cancel", "OK"]


def test_settings_module_does_not_use_qmessagebox():
    import inspect

    import teams_transcriber.ui.settings_dialog as sd
    assert "QMessageBox" not in inspect.getsource(sd)


def _body_label(dlg):
    # title is the first QLabel, body the second
    return dlg.findChildren(QLabel)[1]


def test_body_is_not_selectable_by_default(qtbot):
    dlg = ConfirmDialog(title="T", body="plain")
    qtbot.addWidget(dlg)
    flags = _body_label(dlg).textInteractionFlags()
    assert not (flags & Qt.TextInteractionFlag.TextSelectableByMouse)


def test_selectable_body_can_be_copied(qtbot):
    dlg = ConfirmDialog(title="T", body="a long api error", selectable=True)
    qtbot.addWidget(dlg)
    flags = _body_label(dlg).textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
