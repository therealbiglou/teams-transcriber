from __future__ import annotations

import sys
from pathlib import Path

import pytest

from teams_transcriber import autolaunch


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows-only")
def test_enable_then_disable_round_trip(tmp_path: Path) -> None:
    fake_exe = tmp_path / "ttranscribe.exe"
    fake_exe.write_text("")
    try:
        assert autolaunch.enable(fake_exe)
        assert autolaunch.is_enabled()
    finally:
        autolaunch.disable()
        assert not autolaunch.is_enabled()


def test_disable_noop_when_not_present() -> None:
    """Whether or not we're on Windows, disable should not raise."""
    autolaunch.disable()
