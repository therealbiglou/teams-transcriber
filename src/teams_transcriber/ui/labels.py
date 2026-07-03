"""Shared label helpers: selection flags, the three-guard wrap pattern, the
checkbox+wrapping-label todo row, and a single-line eliding label.

Every view that shows user text should build it from these helpers so wrap /
select / overflow behavior stays consistent app-wide.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QWidget


def make_selectable(label: QLabel) -> QLabel:
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard,
    )
    return label


def make_wrapping(label: QLabel) -> QLabel:
    """The project's three-guard wrap pattern: wordWrap + minWidth 0 + an
    Ignored horizontal policy so a long unbroken token can't push the column
    wider than its container."""
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def make_todo_row(
    text: str,
    *,
    checked: bool,
    on_toggle: Callable[[bool], None],
) -> QWidget:
    """A wrap-friendly todo line: small checkbox + wrapping selectable label.

    QCheckBox's own label does NOT word-wrap — long todo text bleeds past the
    card edge. Splitting into checkbox + sibling wrapping QLabel fixes that;
    the checkbox pins to the top so it aligns with the first wrapped line.
    """
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)

    cb = QCheckBox()
    cb.setChecked(checked)
    cb.toggled.connect(on_toggle)
    h.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)

    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    make_wrapping(label)
    make_selectable(label)
    h.addWidget(label, 1)
    return row


class ElidedLabel(QLabel):
    """Single-line label that elides to its width; full text in the tooltip."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if text:
            self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._update_elide()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        self._update_elide()

    def _update_elide(self) -> None:
        metrics = QFontMetrics(self.font())
        width = max(0, self.width() - 4)
        self.setText(metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width))
