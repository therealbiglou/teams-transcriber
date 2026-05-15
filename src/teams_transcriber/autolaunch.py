"""Auto-launch on Windows via HKCU Run registry key."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

REG_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_VALUE_NAME = "TeamsTranscriber"


def is_enabled() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, REG_VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.exception("could not check auto-launch state")
        return False


def enable(exe_path: Path | str) -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_KEY_PATH, 0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, REG_VALUE_NAME, 0, winreg.REG_SZ, f'"{exe_path}"')
        return True
    except OSError:
        logger.exception("could not enable auto-launch")
        return False


def disable() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_KEY_PATH, 0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, REG_VALUE_NAME)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        logger.exception("could not disable auto-launch")
        return False
