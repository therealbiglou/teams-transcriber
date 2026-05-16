"""Settings dialog with sections: General, Audio, Detection, Transcription, AI."""

from __future__ import annotations

import keyring
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(700, 540)
        self._settings = settings
        self._paths = paths

        outer = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_general_tab(), "General")
        self._tabs.addTab(self._build_audio_tab(), "Audio")
        self._tabs.addTab(self._build_detection_tab(), "Detection")
        self._tabs.addTab(self._build_transcription_tab(), "Transcription")
        self._tabs.addTab(self._build_ai_tab(), "AI")
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
