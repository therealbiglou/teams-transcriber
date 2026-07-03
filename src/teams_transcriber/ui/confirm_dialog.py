"""Themed confirmation dialog — replaces the default Windows QMessageBox.

A frameless rounded card with title + body + Cancel/Confirm buttons. Matches
the app's color palette and button styles so it doesn't look like a system
popup pasted on top of the UI.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.ui.theme import COLORS, RADIUS


class ConfirmDialog(QDialog):
    """Frameless themed yes/no dialog. Drag from anywhere to move."""

    def __init__(
        self,
        *,
        title: str,
        body: str,
        confirm_label: str = "OK",
        cancel_label: str = "Cancel",
        danger: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self._drag_anchor: QPoint | None = None

        # Rounded card
        card = QFrame(self)
        card.setObjectName("ConfirmCard")
        card.setStyleSheet(
            f"#ConfirmCard {{ background: {COLORS['card']}; "
            f"border: 1px solid {COLORS['border']}; "
            f"border-radius: {RADIUS['card']}px; }}"
        )
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 6)
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(12)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 600;")
        title_lbl.setWordWrap(True)
        card_layout.addWidget(title_lbl)

        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setMinimumWidth(0)
        body_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body_lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px;")
        card_layout.addWidget(body_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        cancel_btn = QPushButton(cancel_label)
        cancel_btn.setProperty("role", "secondary")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton(confirm_label)
        confirm_btn.setProperty("role", "danger" if danger else "primary")
        confirm_btn.setDefault(True)
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(confirm_btn)

        card_layout.addLayout(btn_row)

        # Outer layout — margins give the 40px-blur shadow room to render
        # instead of being clipped at the widget edge.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.addWidget(card)

        self.setFixedWidth(480)
        self.adjustSize()

    # Drag-to-move from anywhere on the dialog (since it's frameless and has no titlebar).
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_anchor = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_anchor is not None:
            self.move(e.globalPosition().toPoint() - self._drag_anchor)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        del e
        self._drag_anchor = None

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(e)

    @classmethod
    def ask(
        cls,
        parent: QWidget | None,
        *,
        title: str,
        body: str,
        confirm_label: str = "OK",
        cancel_label: str = "Cancel",
        danger: bool = False,
    ) -> bool:
        """Convenience: show modal, return True if user clicked confirm."""
        dlg = cls(
            title=title, body=body,
            confirm_label=confirm_label, cancel_label=cancel_label,
            danger=danger, parent=parent,
        )
        from teams_transcriber.ui.scrim import exec_modal
        return exec_modal(dlg) == QDialog.DialogCode.Accepted
