from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtWidgets import QDialog, QWidget

from teams_transcriber.ui.scrim import Scrim, exec_modal


def test_scrim_covers_host(qapp):
    host = QWidget()
    host.resize(400, 300)
    s = Scrim(host)
    assert s.parent() is host
    assert s.geometry() == host.rect()
    # Without WA_StyledBackground, a plain QWidget child never paints its
    # stylesheet background — the scrim would be invisible.
    assert s.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)


def test_exec_modal_returns_dialog_result_and_cleans_up(qapp):
    host = QWidget()
    host.resize(400, 300)
    host.show()
    dlg = QDialog(host)

    seen_during_exec: list[Scrim] = []

    def _capture_and_accept() -> None:
        seen_during_exec.extend(host.findChildren(Scrim))
        dlg.accept()

    QTimer.singleShot(0, _capture_and_accept)
    result = exec_modal(dlg)
    assert result == QDialog.DialogCode.Accepted
    # The scrim was present while the dialog was open...
    assert seen_during_exec
    # ...and is destroyed once deferred deletions are processed.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert host.findChildren(Scrim) == []


def test_exec_modal_without_parent_is_safe(qapp):
    dlg = QDialog()
    QTimer.singleShot(0, dlg.reject)
    assert exec_modal(dlg) == QDialog.DialogCode.Rejected
