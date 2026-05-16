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


def _build_launch_command() -> str:
    """Full Windows command-line that launches the UI without a console window."""
    if getattr(sys, "frozen", False):
        # PyInstaller-frozen: sys.executable IS the launcher .exe; no -m flag.
        return f'"{sys.executable}"'
    py = Path(sys.executable)
    pythonw = py.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else py
    return f'"{interpreter}" -m teams_transcriber'


def enable(command: str | None = None) -> bool:
    if not sys.platform.startswith("win"):
        return False
    value = command if command is not None else _build_launch_command()
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_KEY_PATH, 0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, REG_VALUE_NAME, 0, winreg.REG_SZ, value)
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
