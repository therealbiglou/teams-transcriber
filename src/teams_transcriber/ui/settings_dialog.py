"""Settings dialog with sections: General, Audio, Detection, Transcription, AI, Shortcuts."""

from __future__ import annotations

from collections.abc import Callable

import keyring


def _enumerate_microphones() -> list:
    try:
        import soundcard
        return list(soundcard.all_microphones(exclude_monitors=True))
    except Exception:
        return []


def _enumerate_speakers() -> list:
    try:
        import soundcard
        return list(soundcard.all_speakers())
    except Exception:
        return []
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.config import (
    KEYRING_SERVICE,
    KEYRING_USER_ANTHROPIC,
    Settings,
    save_settings,
)
from teams_transcriber.paths import AppPaths


class SettingsDialog(QDialog):
    """Modal settings dialog. Writes to settings.json + keyring on accept."""

    saved = Signal()

    def __init__(
        self,
        settings: Settings,
        paths: AppPaths,
        *,
        hotkey_reload_callback: Callable[[dict[str, str]], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(700, 540)
        self._settings = settings
        self._paths = paths
        self._hotkey_reload_callback = hotkey_reload_callback

        outer = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_general_tab(), "General")
        self._tabs.addTab(self._build_audio_tab(), "Audio")
        self._tabs.addTab(self._build_detection_tab(), "Detection")
        self._tabs.addTab(self._build_transcription_tab(), "Transcription")
        self._tabs.addTab(self._build_ai_tab(), "AI")
        self._tabs.addTab(self._build_shortcuts_tab(), "Shortcuts")
        outer.addWidget(self._tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.auto_launch_cb = QCheckBox()
        self.auto_launch_cb.setChecked(self._settings.auto_launch)
        form.addRow("Auto-launch on Windows startup:", self.auto_launch_cb)
        return w

    def _build_audio_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self._mic_combo = QComboBox()
        self._mic_combo.addItem("Use Windows default", userData=None)
        for mic in _enumerate_microphones():
            self._mic_combo.addItem(mic.name, userData={"id": mic.id, "name": mic.name})

        self._loopback_combo = QComboBox()
        self._loopback_combo.addItem("Use Windows default", userData=None)
        for spk in _enumerate_speakers():
            self._loopback_combo.addItem(spk.name, userData={"id": spk.id, "name": spk.name})

        # Preselect from settings.
        saved_mic = self._settings.audio_mic_device
        if saved_mic is not None:
            for i in range(self._mic_combo.count()):
                d = self._mic_combo.itemData(i)
                if d and d.get("id") == saved_mic.get("id"):
                    self._mic_combo.setCurrentIndex(i)
                    break
        saved_loop = self._settings.audio_loopback_device
        if saved_loop is not None:
            for i in range(self._loopback_combo.count()):
                d = self._loopback_combo.itemData(i)
                if d and d.get("id") == saved_loop.get("id"):
                    self._loopback_combo.setCurrentIndex(i)
                    break

        form.addRow("Microphone:", self._mic_combo)
        form.addRow("System audio source:", self._loopback_combo)

        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(0, 3650)
        self.retention_spin.setSuffix(" days")
        self.retention_spin.setValue(self._settings.audio_retention_days)
        form.addRow("Audio retention:", self.retention_spin)
        return w

    def _build_detection_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        layout.addWidget(QLabel("Teams window-title patterns (case-insensitive substring match):"))

        self.pattern_list = QListWidget()
        for p in self._settings.detection_title_patterns:
            self.pattern_list.addItem(QListWidgetItem(p))
        layout.addWidget(self.pattern_list, 1)

        row = QHBoxLayout()
        self.pattern_input = QLineEdit()
        self.pattern_input.setPlaceholderText("Add a pattern (e.g. 'Meeting with ')")
        row.addWidget(self.pattern_input, 1)
        add_btn = QPushButton("Add")
        add_btn.setProperty("role", "secondary")
        add_btn.clicked.connect(self._add_pattern)
        row.addWidget(add_btn)
        remove_btn = QPushButton("Remove")
        remove_btn.setProperty("role", "ghost")
        remove_btn.clicked.connect(self._remove_pattern)
        row.addWidget(remove_btn)
        layout.addLayout(row)
        return w

    def _build_transcription_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.model_combo = QComboBox()
        self.model_combo.addItems(["large-v3-turbo", "large-v3", "medium", "small"])
        idx = self.model_combo.findText(self._settings.transcription_model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        form.addRow("Whisper model:", self.model_combo)

        self.compute_combo = QComboBox()
        self.compute_combo.addItems(["int8_float16", "float16", "int8"])
        idx = self.compute_combo.findText(self._settings.transcription_compute_type)
        if idx >= 0:
            self.compute_combo.setCurrentIndex(idx)
        form.addRow("Compute type:", self.compute_combo)
        return w

    def _build_ai_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.ai_model_combo = QComboBox()
        self.ai_model_combo.addItems(["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5"])
        idx = self.ai_model_combo.findText(self._settings.ai_model)
        if idx >= 0:
            self.ai_model_combo.setCurrentIndex(idx)
        form.addRow("Claude model:", self.ai_model_combo)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        current = self._settings.anthropic_api_key() or ""
        if current:
            self.api_key_input.setPlaceholderText(f"(stored: {current[:14]}…)")
        form.addRow("Anthropic API key:", self.api_key_input)

        self.addendum_input = QTextEdit()
        self.addendum_input.setPlainText(self._settings.ai_custom_prompt_addendum)
        self.addendum_input.setFixedHeight(80)
        form.addRow("Custom prompt addendum:", self.addendum_input)
        return w

    def _build_shortcuts_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        shortcuts_group = QGroupBox("Shortcuts")
        shortcuts_form = QFormLayout(shortcuts_group)
        self._hotkey_inputs: dict[str, QLineEdit] = {}
        for key, label, default in [
            ("toggle_manual_recording", "Toggle recording",        "ctrl+alt+r"),
            ("open_workspace",          "Open workspace",          "ctrl+alt+n"),
            ("toggle_pause_detection",  "Pause/unpause detection", "ctrl+alt+p"),
        ]:
            row = QHBoxLayout()
            line = QLineEdit(self._settings.hotkeys.get(key, default))
            line.setPlaceholderText(default)
            row.addWidget(line, 1)
            reset = QPushButton("Reset")
            reset.setProperty("role", "ghost")
            reset.setFixedWidth(60)
            reset.clicked.connect(lambda _checked=False, ln=line, d=default: ln.setText(d))
            row.addWidget(reset)
            wrapper = QWidget()
            wrapper.setLayout(row)
            shortcuts_form.addRow(label, wrapper)
            self._hotkey_inputs[key] = line
        outer.addWidget(shortcuts_group)
        outer.addStretch(1)
        return w

    def _add_pattern(self) -> None:
        text = self.pattern_input.text().strip()
        if not text:
            return
        self.pattern_list.addItem(QListWidgetItem(text))
        self.pattern_input.clear()

    def _remove_pattern(self) -> None:
        for item in self.pattern_list.selectedItems():
            self.pattern_list.takeItem(self.pattern_list.row(item))

    def _on_accept(self) -> None:
        s = self._settings
        s._raw["general"]["auto_launch"] = self.auto_launch_cb.isChecked()
        s._raw["audio"]["retention_days"] = self.retention_spin.value()
        # Audio device selections — Phase 6.
        s._raw["audio"]["mic_device"] = self._mic_combo.currentData()
        s._raw["audio"]["loopback_device"] = self._loopback_combo.currentData()
        patterns: list[str] = []
        for i in range(self.pattern_list.count()):
            item = self.pattern_list.item(i)
            if item is not None:
                patterns.append(item.text())
        s._raw["detection"]["title_patterns"] = patterns
        s._raw["transcription"]["model"] = self.model_combo.currentText()
        s._raw["transcription"]["compute_type"] = self.compute_combo.currentText()
        s._raw["ai"]["model"] = self.ai_model_combo.currentText()
        s._raw["ai"]["custom_prompt_addendum"] = self.addendum_input.toPlainText()

        # Hotkeys — Phase 5.
        s._raw["hotkeys"] = dict(s._raw.get("hotkeys", {}))
        new_hotkeys: dict[str, str] = {}
        for key, line in self._hotkey_inputs.items():
            value = line.text().strip()
            if not value:
                value = s._raw["hotkeys"].get(key, "")
            new_hotkeys[key] = value
            s._raw["hotkeys"][key] = value

        save_settings(self._paths, s)

        from teams_transcriber import autolaunch
        if self.auto_launch_cb.isChecked():
            autolaunch.enable()
        else:
            autolaunch.disable()

        new_key = self.api_key_input.text().strip()
        if new_key:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER_ANTHROPIC, new_key)

        self.saved.emit()
        self.accept()

        if self._hotkey_reload_callback is not None:
            self._hotkey_reload_callback(new_hotkeys)
