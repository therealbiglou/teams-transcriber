"""Persist window geometry and splitter layout across sessions.

Backed by QSettings (HKCU registry on Windows). Every top-level window gets a
stable string key; `saveGeometry`/`restoreGeometry` handle multi-monitor,
DPI, and off-screen validation for us.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QSplitter, QWidget

_ORG = "Teams Transcriber"
_APP = "Teams Transcriber"


def _store(settings: QSettings | None) -> QSettings:
    return settings if settings is not None else QSettings(_ORG, _APP)


def restore_window_geometry(
    window: QWidget,
    key: str,
    *,
    default_size: tuple[int, int] | None = None,
    settings: QSettings | None = None,
) -> bool:
    data = _store(settings).value(f"geometry/{key}")
    if isinstance(data, QByteArray) and not data.isEmpty() and window.restoreGeometry(data):
        return True
    if default_size is not None:
        window.resize(*default_size)
    return False


def save_window_geometry(
    window: QWidget, key: str, *, settings: QSettings | None = None,
) -> None:
    _store(settings).setValue(f"geometry/{key}", window.saveGeometry())


def restore_splitter_state(
    splitter: QSplitter, key: str, *, settings: QSettings | None = None,
) -> bool:
    data = _store(settings).value(f"splitter/{key}")
    return (
        isinstance(data, QByteArray)
        and not data.isEmpty()
        and splitter.restoreState(data)
    )


def save_splitter_state(
    splitter: QSplitter, key: str, *, settings: QSettings | None = None,
) -> None:
    _store(settings).setValue(f"splitter/{key}", splitter.saveState())
