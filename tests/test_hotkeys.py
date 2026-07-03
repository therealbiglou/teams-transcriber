from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from teams_transcriber.ui.hotkeys import HotkeyManager


def test_register_calls_keyboard_add_hotkey(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []

    def fake_add(hk: str, cb: Any) -> int:
        calls.append((hk, cb))
        return 42

    fake_kb = SimpleNamespace(add_hotkey=fake_add, remove_hotkey=lambda _h: None)
    monkeypatch.setitem(sys.modules, "keyboard", fake_kb)

    mgr = HotkeyManager()
    triggered: list[int] = []
    ok = mgr.register("ctrl+alt+r", lambda: triggered.append(1))
    assert ok is True
    assert calls[0][0] == "ctrl+alt+r"
    calls[0][1]()
    assert triggered == [1]


def test_stop_removes_hotkeys(monkeypatch: pytest.MonkeyPatch) -> None:
    removed: list[int] = []
    fake_kb = SimpleNamespace(add_hotkey=lambda *_a: 99, remove_hotkey=removed.append)
    monkeypatch.setitem(sys.modules, "keyboard", fake_kb)

    mgr = HotkeyManager()
    mgr.register("ctrl+alt+r", lambda: None)
    mgr.stop()
    assert removed == [99]


def test_hotkey_manager_reload_replaces_bindings(monkeypatch) -> None:
    """After reload(), only the new bindings should fire."""
    from teams_transcriber.ui.hotkeys import HotkeyManager

    calls: list[str] = []
    fake_module_state: dict = {"hotkeys": {}}

    class _FakeKeyboard:
        def add_hotkey(self, hotkey, callback):
            fake_module_state["hotkeys"][hotkey] = callback
            calls.append(f"add:{hotkey}")
            return hotkey

        def remove_hotkey(self, handle):
            fake_module_state["hotkeys"].pop(handle, None)
            calls.append(f"remove:{handle}")

    fake = _FakeKeyboard()
    mgr = HotkeyManager()
    monkeypatch.setattr(mgr, "_keyboard", fake)

    mgr.register("ctrl+alt+r", lambda: None)
    assert "add:ctrl+alt+r" in calls

    mgr.reload([
        ("ctrl+alt+n", lambda: None),
        ("ctrl+alt+p", lambda: None),
    ])
    assert "remove:ctrl+alt+r" in calls
    assert "add:ctrl+alt+n" in calls
    assert "add:ctrl+alt+p" in calls
