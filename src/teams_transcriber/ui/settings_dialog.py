"""Settings dialog with sections: General, Audio, Detection, Transcription, AI, Shortcuts."""

from __future__ import annotations

from collections.abc import Callable

import keyring


def _enumerate_microphones() -> list:
    try:
        import soundcard
        return list(soundcard.all_microphones(include_loopback=False))
    except Exception:
        return []


def _enumerate_speakers() -> list:
    try:
        import soundcard
        return list(soundcard.all_speakers())
    except Exception:
        return []
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
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
from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.title_bar import TitleBar


class SettingsDialog(FramelessWindowMixin, QDialog):
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
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.resize(700, 540)
        self._settings = settings
        self._paths = paths
        self._hotkey_reload_callback = hotkey_reload_callback

        frame = QFrame()
        frame.setObjectName("OuterFrame")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.addWidget(frame)

        inner = QVBoxLayout(frame)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        self._title_bar = TitleBar(title="Settings", controls=("max", "close"))
        self._title_bar.maximize_requested.connect(self.toggle_max)
        self._title_bar.close_requested.connect(self.reject)
        inner.addWidget(self._title_bar)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 16)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_general_tab(), "General")
        self._tabs.addTab(self._build_audio_tab(), "Audio")
        self._tabs.addTab(self._build_detection_tab(), "Detection")
        self._tabs.addTab(self._build_transcription_tab(), "Transcription")
        self._tabs.addTab(self._build_ai_tab(), "AI")
        self._tabs.addTab(self._build_shortcuts_tab(), "Shortcuts")
        self._tabs.addTab(self._build_about_tab(), "About")
        body_layout.addWidget(self._tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        body_layout.addWidget(buttons)

        inner.addWidget(body, 1)

        self._init_frameless(frame, resizable=True, title_bar=self._title_bar)

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

        self._live_enabled_check = QCheckBox("Stream transcription during recording (experimental)")
        self._live_enabled_check.setChecked(self._settings.transcription_live_enabled)
        form.addRow("", self._live_enabled_check)

        self._redownload_model_btn = QPushButton("Re-download Whisper model")
        self._redownload_model_btn.setProperty("role", "secondary")
        self._redownload_model_btn.clicked.connect(self._redownload_model)
        form.addRow("", self._redownload_model_btn)
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

    def _build_about_tab(self) -> QWidget:
        from teams_transcriber import __version__

        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(12)

        title = QLabel("<h2>Teams Transcriber</h2>")
        v.addWidget(title)

        version_label = QLabel(f"Version: <b>{__version__}</b>")
        v.addWidget(version_label)

        last_check = self._settings.last_update_check or "Never"
        self._last_check_label = QLabel(f"Last update check: {last_check}")
        v.addWidget(self._last_check_label)

        # Auto-check checkbox.
        self._auto_check_cb = QCheckBox("Automatically check for updates on startup")
        self._auto_check_cb.setChecked(self._settings.auto_check_updates)
        v.addWidget(self._auto_check_cb)

        # "Check for updates now" button.
        check_btn = QPushButton("Check for updates now")
        check_btn.setProperty("role", "primary")
        check_btn.clicked.connect(self._manual_update_check)
        v.addWidget(check_btn)

        self._update_check_result = QLabel("")
        self._update_check_result.setWordWrap(True)
        v.addWidget(self._update_check_result)

        # Shown only when a check finds a newer version; installs in-app.
        self._latest_release = None
        self._install_btn = QPushButton("Install update")
        self._install_btn.setProperty("role", "primary")
        self._install_btn.setVisible(False)
        self._install_btn.clicked.connect(self._install_update)
        v.addWidget(self._install_btn)

        v.addStretch(1)
        return w

    def _install_update(self) -> None:
        """Download + install the update found by the last check, from Settings."""
        if self._latest_release is None:
            return
        from teams_transcriber.ui.update_dialog import UpdateDialog
        dlg = UpdateDialog(
            version=self._latest_release.tag,
            download_url=self._latest_release.installer_url,
            paths=self._paths,
            parent=self,
        )
        dlg.exec()

    def _manual_update_check(self) -> None:
        """Runs update_checker.fetch_latest_release synchronously, updates UI."""
        from teams_transcriber import __version__
        from teams_transcriber.update_checker import (
            UpdateCheckError,
            fetch_latest_release,
            is_update_available,
        )
        from datetime import datetime, UTC

        self._update_check_result.setText("Checking…")
        QApplication.processEvents()  # let the label repaint
        try:
            latest = fetch_latest_release()
        except UpdateCheckError as exc:
            self._update_check_result.setText(f"<span style='color: #DC2626;'>Check failed: {exc}</span>")
            return
        ts = datetime.now(UTC).strftime("%Y-%m-%d %I:%M %p UTC")
        self._last_check_label.setText(f"Last update check: {ts}")
        if is_update_available(__version__, latest):
            self._latest_release = latest
            self._update_check_result.setText(
                f"<b>Update available: {latest.tag}</b><br>"
                f"<a href='{latest.html_url}'>View release notes</a>"
            )
            self._update_check_result.setOpenExternalLinks(True)
            self._install_btn.setVisible(True)
        else:
            self._latest_release = None
            self._install_btn.setVisible(False)
            self._update_check_result.setText("You're on the latest version.")

    def _redownload_model(self) -> None:
        """Wipe the cached Whisper model snapshot dir and re-download.

        Used to recover from broken downloads (missing model.bin etc.).
        """
        import shutil
        from pathlib import Path
        from PySide6.QtWidgets import QMessageBox
        from teams_transcriber.ui.confirm_dialog import ConfirmDialog

        repo_id = self._settings.transcription_model
        cache_root = Path.home() / ".cache" / "huggingface" / "hub"
        target_marker = repo_id.replace("/", "--")
        candidates: list[Path] = []
        if cache_root.is_dir():
            for d in cache_root.iterdir():
                if d.is_dir() and target_marker in d.name:
                    candidates.append(d)
                elif d.is_dir() and "faster-whisper" in d.name:
                    candidates.append(d)
        # Deduplicate while preserving order.
        seen: set[Path] = set()
        unique_candidates: list[Path] = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique_candidates.append(c)
        candidates = unique_candidates

        if not candidates:
            QMessageBox.information(
                self, "Model cache not found",
                f"Could not find a cached Whisper model under {cache_root}.\n"
                "The model will download fresh on next transcription.",
            )
            return

        names = "\n".join(f"  • {d.name}" for d in candidates)
        ok = ConfirmDialog.ask(
            self, title="Re-download Whisper model?",
            body=(
                "This will delete the following cached model directories "
                "so they re-download (~3 GB) on next transcription:\n\n"
                f"{names}"
            ),
            confirm_label="Re-download", cancel_label="Cancel", danger=True,
        )
        if not ok:
            return

        deleted_any = False
        for d in candidates:
            try:
                shutil.rmtree(d)
                deleted_any = True
            except OSError as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Could not delete %s: %s", d, exc,
                )
        if deleted_any:
            QMessageBox.information(
                self, "Done",
                "Model cache cleared. The model will re-download on the "
                "next transcription. Use Retry on any failed recordings "
                "to trigger it now.",
            )

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
        # General — auto_check_updates (new).
        s._raw["general"]["auto_check_updates"] = self._auto_check_cb.isChecked()
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
        s._raw["transcription"]["live_enabled"] = self._live_enabled_check.isChecked()
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
