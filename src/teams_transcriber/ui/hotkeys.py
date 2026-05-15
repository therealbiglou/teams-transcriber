"""Global hotkey registration using the `keyboard` library."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class HotkeyManager:
    """Owns registered hotkeys and removes them cleanly on stop()."""

    def __init__(self) -> None:
        self._registered: list[int] = []
        self._keyboard: Any = None

    def _try_import(self) -> bool:
        if self._keyboard is not None:
            return True
        try:
            import keyboard
            self._keyboard = keyboard
            return True
        except ImportError:
            logger.warning("keyboard module not available; hotkeys disabled")
            return False

    def register(self, hotkey: str, callback: Callable[[], None]) -> bool:
        """Register a hotkey like 'ctrl+alt+r'. Returns True on success."""
        if not self._try_import():
            return False
        try:
            handle = self._keyboard.add_hotkey(hotkey, callback)
            self._registered.append(handle)
            return True
        except Exception:
            logger.exception("failed to register hotkey %r", hotkey)
            return False

    def stop(self) -> None:
        if self._keyboard is None:
            return
        for h in self._registered:
            try:
                self._keyboard.remove_hotkey(h)
            except Exception:
                logger.exception("failed to remove hotkey %s", h)
        self._registered.clear()
