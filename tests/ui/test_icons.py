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


def test_render_state_icon_processing_differs_from_idle(qapp) -> None:
    from PySide6.QtCore import QBuffer, QIODevice

    def _icon_bytes(state):
        icon = render_state_icon(state)
        pixmap = icon.pixmap(32, 32)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.ReadWrite)
        pixmap.save(buffer, "PNG")
        return bytes(buffer.data())

    idle = _icon_bytes(TrayState.IDLE)
    processing = _icon_bytes(TrayState.PROCESSING)
    assert idle != processing, "PROCESSING icon must differ from IDLE"
