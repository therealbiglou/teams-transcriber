from __future__ import annotations

from PySide6.QtWidgets import QSystemTrayIcon

from teams_transcriber.ui.icons import TrayState
from teams_transcriber.ui.tray import AppTray


def test_tray_constructs_with_idle_state(qapp, qtbot) -> None:
    tray = AppTray()
    assert isinstance(tray, QSystemTrayIcon)
    assert tray.state == TrayState.IDLE
    assert tray.toolTip().startswith("Teams Transcriber")


def test_set_state_updates_tooltip(qapp, qtbot) -> None:
    tray = AppTray()
    tray.set_state(TrayState.RECORDING, label="Q2 sync")
    assert tray.state == TrayState.RECORDING
    assert "Recording" in tray.toolTip()
    assert "Q2 sync" in tray.toolTip()


def test_menu_actions_emit_signals(qapp, qtbot) -> None:
    tray = AppTray()
    # Force into recording state so the notes action is enabled and triggerable.
    tray.set_state(TrayState.RECORDING, label="x")

    received: list[str] = []
    tray.start_manual_requested.connect(lambda: received.append("start"))
    tray.stop_manual_requested.connect(lambda: received.append("stop"))
    tray.open_window_requested.connect(lambda: received.append("open"))
    tray.pause_detection_toggled.connect(lambda v: received.append(f"pause={v}"))
    tray.open_workspace_requested.connect(lambda: received.append("notes"))
    tray.quit_requested.connect(lambda: received.append("quit"))

    # start is disabled while recording, so trigger it after flipping state back.
    tray.set_state(TrayState.IDLE)
    tray.start_action.trigger()
    tray.set_state(TrayState.RECORDING, label="x")
    tray.stop_action.trigger()
    tray.notes_action.trigger()
    tray.open_action.trigger()
    tray.pause_action.trigger()  # toggles
    tray.quit_action.trigger()

    assert received == ["start", "stop", "notes", "open", "pause=True", "quit"]
