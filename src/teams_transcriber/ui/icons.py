"""Programmatic SVG icon factory."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from teams_transcriber.ui.theme import COLORS


class IconName(Enum):
    RECORD = "record"
    STOP = "stop"
    PAUSE = "pause"
    PLAY = "play"
    SETTINGS = "settings"
    SEARCH = "search"
    CLOSE = "close"
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    RESTORE = "restore"
    CHEVRON_RIGHT = "chevron-right"
    CHEVRON_DOWN = "chevron-down"
    DOT = "dot"
    CHECK = "check"
    COPY = "copy"
    EXPORT = "export"


class TrayState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    ERROR = "error"


# 24x24 viewbox, Lucide-style stroke icons.
_PATHS: dict[IconName, str] = {
    IconName.RECORD:        '<circle cx="12" cy="12" r="6" fill="currentColor"/>',
    IconName.STOP:          '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
    IconName.PAUSE:         '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor"/>'
                            '<rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor"/>',
    IconName.PLAY:          '<path d="M7 5l12 7-12 7z" fill="currentColor"/>',
    IconName.SETTINGS:      '<circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.5" fill="none"/>'
                            '<path d="M12 1v6m0 10v6M4.22 4.22l4.24 4.24m7.08 7.08l4.24 4.24M1 12h6m10 0h6M4.22 19.78l4.24-4.24m7.08-7.08l4.24-4.24" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" fill="none"/>',
    IconName.SEARCH:        '<circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.5" fill="none"/>'
                            '<path d="M21 21l-4.35-4.35" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    IconName.CLOSE:         '<path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    IconName.MINIMIZE:      '<path d="M5 12h14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    IconName.MAXIMIZE:      '<rect x="5" y="5" width="14" height="14" rx="1" stroke="currentColor" stroke-width="1.5" fill="none"/>',
    IconName.RESTORE:       '<rect x="7" y="3" width="14" height="14" rx="1" stroke="currentColor" stroke-width="1.5" fill="none"/>'
                            '<rect x="3" y="7" width="14" height="14" rx="1" stroke="currentColor" stroke-width="1.5" fill="none"/>',
    IconName.CHEVRON_RIGHT: '<path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    IconName.CHEVRON_DOWN:  '<path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    IconName.DOT:           '<circle cx="12" cy="12" r="4" fill="currentColor"/>',
    IconName.CHECK:         '<path d="M5 12l4 4 10-10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    IconName.COPY:          '<rect x="9" y="9" width="11" height="11" rx="2" stroke="currentColor" stroke-width="1.5" fill="none"/>'
                            '<path d="M5 15V5a2 2 0 012-2h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" fill="none"/>',
    IconName.EXPORT:        '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
}


def _build_svg(name: IconName, color: str) -> bytes:
    body = _PATHS[name].replace("currentColor", color)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'
    return svg.encode("utf-8")


def get_icon(name: IconName, *, color: str | None = None) -> QIcon:
    """Return a QIcon for the given symbolic icon, optionally re-colored."""
    color = color or COLORS["text_primary"]
    svg = _build_svg(name, color)
    renderer = QSvgRenderer(QByteArray(svg))
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def render_state_icon(state: TrayState, size: int = 32) -> QIcon:
    """Render a tray-icon variant: a solid filled circle in the state's color."""
    color = {
        TrayState.IDLE:       COLORS["accent"],       # emerald #10B981
        TrayState.RECORDING:  COLORS["red"],           # red #EF4444
        TrayState.PROCESSING: COLORS["amber"],         # amber #F59E0B
        TrayState.ERROR:      COLORS["text_tertiary"], # gray #9CA3AF
    }[state]
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' \
          f'<circle cx="12" cy="12" r="8" fill="{color}"/></svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
