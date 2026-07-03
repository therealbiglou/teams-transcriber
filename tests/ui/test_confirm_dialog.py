from __future__ import annotations

from PySide6.QtWidgets import QPushButton

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
