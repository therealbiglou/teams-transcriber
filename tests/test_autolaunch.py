from __future__ import annotations

import sys
from pathlib import Path

import pytest

from teams_transcriber import autolaunch


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows-only")
def test_enable_then_disable_round_trip(tmp_path: Path) -> None:
    fake_command = f'"{tmp_path / "ttranscribe.exe"}" -m teams_transcriber'
    try:
        assert autolaunch.enable(fake_command)
        assert autolaunch.is_enabled()
    finally:
        autolaunch.disable()
        assert not autolaunch.is_enabled()


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows-only")
def test_enable_default_command_uses_module_invocation() -> None:
    """Default command must invoke the module, not bare python (the REPL bug)."""
    import winreg

    try:
        assert autolaunch.enable()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, autolaunch.REG_KEY_PATH, 0, winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, autolaunch.REG_VALUE_NAME)
        assert "-m teams_transcriber" in value
        assert value.startswith('"')
    finally:
        autolaunch.disable()


def test_disable_noop_when_not_present() -> None:
    """Whether or not we're on Windows, disable should not raise."""
    autolaunch.disable()
