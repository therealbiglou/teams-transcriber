from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QWidget

from teams_transcriber.ui.scrim import Scrim, exec_modal


def test_scrim_covers_host(qapp):
    host = QWidget()
    host.resize(400, 300)
    s = Scrim(host)
    assert s.parent() is host
    assert s.geometry() == host.rect()


def test_exec_modal_returns_dialog_result_and_cleans_up(qapp):
    host = QWidget()
    host.resize(400, 300)
    host.show()
    dlg = QDialog(host)
    QTimer.singleShot(0, dlg.accept)
    result = exec_modal(dlg)
    assert result == QDialog.DialogCode.Accepted


def test_exec_modal_without_parent_is_safe(qapp):
    dlg = QDialog()
    QTimer.singleShot(0, dlg.reject)
    assert exec_modal(dlg) == QDialog.DialogCode.Rejected
