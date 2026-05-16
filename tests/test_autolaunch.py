from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

from teams_transcriber import autolaunch


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch: pytest.MonkeyPatch):
    """Route autolaunch tests to a per-test HKCU subkey so they never touch
    the real Run key (which would clobber the user's actual autolaunch entry)."""
    if not sys.platform.startswith("win"):
        yield
        return
    import winreg
    test_key_path = rf"Software\TeamsTranscriberTests\{uuid.uuid4().hex}"
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, test_key_path)
    monkeypatch.setattr(autolaunch, "REG_KEY_PATH", test_key_path)
    try:
        yield
    finally:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, test_key_path, 0, winreg.KEY_SET_VALUE,
            ) as k:
                try:
                    winreg.DeleteValue(k, autolaunch.REG_VALUE_NAME)
                except FileNotFoundError:
                    pass
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, test_key_path)
        except OSError:
            pass


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


def test_build_launch_command_frozen_uses_sys_executable_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """When PyInstaller-frozen, the Run-key value is the exe path with no -m flag."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\TeamsTranscriber\TeamsTranscriber.exe")
    cmd = autolaunch._build_launch_command()
    assert cmd == r'"C:\Apps\TeamsTranscriber\TeamsTranscriber.exe"'
    assert "-m teams_transcriber" not in cmd


def test_build_launch_command_source_uses_module_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Source mode keeps the pythonw -m teams_transcriber form."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd = autolaunch._build_launch_command()
    assert "-m teams_transcriber" in cmd
