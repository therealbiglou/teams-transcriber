"""In-app toast banner — a frameless rounded popup near the bottom-right of the screen.

Bypasses Windows' OS notification system (which requires AppUserModelID
registration and often silently buries unregistered apps' toasts in the
Action Center). This widget is always visible while displayed.

Usage:
    banner = ToastBanner.show_toast(title="Recording started",
                                    body="House of Blues meeting",
                                    action_label="Add notes",
                                    action_callback=lambda: open_notes(rid))
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable

from PySide6.QtCore import QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.ui.icons import IconName, get_icon
from teams_transcriber.ui.theme import COLORS, RADIUS

logger = logging.getLogger(__name__)

_ACTIVE_TOASTS: list[ToastBanner] = []


class ToastBanner(QFrame):
    """A small frameless popup. Stacks above any earlier active toasts."""

    def __init__(
        self,
        *,
        title: str,
        body: str,
        action_label: str | None = None,
        action_callback: Callable[[], None] | None = None,
        duration_ms: int = 6000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Outer container provides the rounded card.
        outer = QFrame(self)
        outer.setObjectName("ToastOuter")
        outer.setStyleSheet(
            f"#ToastOuter {{ background: {COLORS['card']}; "
            f"border: 1px solid {COLORS['border']}; "
            f"border-radius: {RADIUS['card']}px; }}"
        )

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(28)
        shadow.setColor(QColor(0, 0, 0, 50))
        shadow.setOffset(0, 4)
        outer.setGraphicsEffect(shadow)

        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(16, 14, 12, 14)
        outer_layout.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        text_col.addWidget(title_lbl)
        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setMinimumWidth(0)
        body_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body_lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px;")
        text_col.addWidget(body_lbl)
        outer_layout.addLayout(text_col, 1)

        if action_label is not None and action_callback is not None:
            action_btn = QPushButton(action_label)
            action_btn.setProperty("role", "primary")
            action_btn.clicked.connect(action_callback)
            action_btn.clicked.connect(self._dismiss)
            outer_layout.addWidget(action_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        close_btn = QPushButton()
        close_btn.setIcon(get_icon(IconName.CLOSE, color=COLORS["text_tertiary"]))
        close_btn.setFixedSize(24, 24)
        close_btn.setProperty("role", "ghost")
        close_btn.clicked.connect(self._dismiss)
        outer_layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)

        # Top-level layout wraps the outer card with margins so the 28px-blur
        # shadow has room to render instead of being clipped.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(outer)

        self.setFixedWidth(412)
        self.adjustSize()

        # Auto-dismiss timer.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)
        self._timer.start(duration_ms)

        self._fade: QPropertyAnimation | None = None

    # --- positioning -------------------------------------------------------

    def show_at_bottom_right(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.show()
            return
        geom = screen.availableGeometry()
        margin = 16
        # Stack above earlier toasts.
        offset_y = sum(t.height() + 8 for t in _ACTIVE_TOASTS)
        x = geom.right() - self.width() - margin
        y = geom.bottom() - self.height() - margin - offset_y
        self.setGeometry(QRect(x, y, self.width(), self.height()))
        self.show()
        _ACTIVE_TOASTS.append(self)

    # --- dismissal --------------------------------------------------------

    def _dismiss(self) -> None:
        if self._fade is not None:
            return
        with contextlib.suppress(ValueError):
            _ACTIVE_TOASTS.remove(self)
        # Fade-out animation
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(180)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.close)
        self._fade.start()


def show_in_app_toast(
    title: str,
    body: str,
    *,
    action_label: str | None = None,
    action_callback: Callable[[], None] | None = None,
    duration_ms: int = 6000,
) -> ToastBanner | None:
    """Construct + show a ToastBanner. Returns the banner for tests."""
    app = QApplication.instance()
    if app is None:
        logger.warning("show_in_app_toast called with no QApplication")
        return None
    banner = ToastBanner(
        title=title, body=body,
        action_label=action_label, action_callback=action_callback,
        duration_ms=duration_ms,
    )
    banner.show_at_bottom_right()
    return banner
