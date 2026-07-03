"""Dim-the-parent overlay for modal dialogs — a depth cue that makes it obvious
which window is interactive. Use exec_modal(dlg) instead of dlg.exec()."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QWidget

from teams_transcriber.ui.theme import RADIUS


class Scrim(QWidget):
    """Semi-transparent rounded overlay covering a host widget."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # Plain QWidget children don't paint stylesheet backgrounds without this.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"background: rgba(31, 41, 55, 0.35); "
            f"border-radius: {RADIUS['window']}px;"
        )
        self.setGeometry(host.rect())
        self.show()
        self.raise_()


def _scrim_host(parent: QWidget | None) -> QWidget | None:
    if parent is None:
        return None
    win = parent.window()
    outer = getattr(win, "_outer", None)   # FramelessWindowMixin hosts
    return outer if outer is not None else win


def exec_modal(dialog: QDialog) -> int:
    """dialog.exec() with a dimming scrim over the parent window."""
    host = _scrim_host(dialog.parentWidget())
    scrim = Scrim(host) if host is not None else None
    try:
        return dialog.exec()
    finally:
        if scrim is not None:
            scrim.deleteLater()
