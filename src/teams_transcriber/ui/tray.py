"""System tray icon with state and a right-click menu."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from teams_transcriber.ui.icons import IconName, TrayState, get_icon, render_state_icon


class AppTray(QSystemTrayIcon):
    """State-driven tray icon."""

    start_manual_requested = Signal()
    stop_manual_requested = Signal()
    open_window_requested = Signal()
    pause_detection_toggled = Signal(bool)
    open_workspace_requested = Signal()  # open workspace for current recording
    quit_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.state: TrayState = TrayState.IDLE
        self._current_recording_title: str | None = None

        self.setIcon(render_state_icon(TrayState.IDLE))
        self.setToolTip("Teams Transcriber — idle")

        self._build_menu()
        self.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        menu = QMenu()
        self.open_action = QAction(get_icon(IconName.CHEVRON_RIGHT), "Open Teams Transcriber", self)
        self.open_action.triggered.connect(self.open_window_requested.emit)
        menu.addAction(self.open_action)

        menu.addSeparator()

        self.start_action = QAction(get_icon(IconName.RECORD), "Start manual recording", self)
        self.start_action.triggered.connect(self.start_manual_requested.emit)
        menu.addAction(self.start_action)

        self.stop_action = QAction(get_icon(IconName.STOP), "Stop recording", self)
        self.stop_action.triggered.connect(self.stop_manual_requested.emit)
        menu.addAction(self.stop_action)

        self.notes_action = QAction(
            get_icon(IconName.COPY), "Open workspace…", self,
        )
        self.notes_action.triggered.connect(self.open_workspace_requested.emit)
        self.notes_action.setEnabled(False)
        menu.addAction(self.notes_action)

        menu.addSeparator()

        self.pause_action = QAction(get_icon(IconName.PAUSE), "Pause auto-detection", self)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self.pause_detection_toggled.emit)
        menu.addAction(self.pause_action)

        menu.addSeparator()

        self.settings_action = QAction(get_icon(IconName.SETTINGS), "Settings", self)
        # Settings wiring lives in the app; here we just expose the action.
        menu.addAction(self.settings_action)

        self.quit_action = QAction(get_icon(IconName.CLOSE), "Quit", self)
        self.quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(self.quit_action)

        self.setContextMenu(menu)

    def set_state(self, state: TrayState, *, label: str | None = None) -> None:
        self.state = state
        self.setIcon(render_state_icon(state))
        self._current_recording_title = label
        labels = {
            TrayState.IDLE:       "idle",
            TrayState.RECORDING:  f"Recording — {label or 'meeting'}",
            TrayState.PROCESSING: "Processing",
            TrayState.ERROR:      "Last operation failed",
        }
        self.setToolTip(f"Teams Transcriber — {labels[state]}")

        # Menu state per the design table:
        # Start: IDLE or ERROR. Stop: RECORDING only.
        # Open workspace: RECORDING or PROCESSING.
        self.start_action.setEnabled(state in (TrayState.IDLE, TrayState.ERROR))
        self.stop_action.setEnabled(state == TrayState.RECORDING)
        self.notes_action.setEnabled(state in (TrayState.RECORDING, TrayState.PROCESSING))

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_window_requested.emit()
