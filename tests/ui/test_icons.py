from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon

from teams_transcriber.ui.icons import IconName, TrayState, get_icon, render_state_icon


def test_get_icon_returns_qicon_for_each_name() -> None:
    for name in IconName:
        icon = get_icon(name)
        assert isinstance(icon, QIcon)
        pixmap = icon.pixmap(QSize(16, 16))
        assert not pixmap.isNull(), f"icon {name} produced null pixmap"


def test_get_icon_accepts_color_override() -> None:
    red = get_icon(IconName.RECORD, color="#FF0000")
    green = get_icon(IconName.RECORD, color="#00FF00")
    assert not red.pixmap(QSize(16, 16)).isNull()
    assert not green.pixmap(QSize(16, 16)).isNull()


def test_render_state_icon_for_all_states() -> None:
    for state in TrayState:
        icon = render_state_icon(state)
        assert isinstance(icon, QIcon)
        assert not icon.pixmap(QSize(32, 32)).isNull()
