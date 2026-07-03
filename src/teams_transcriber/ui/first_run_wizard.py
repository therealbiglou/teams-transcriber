"""First-run wizard shown on initial app launch.

Three pages: welcome, account setup (API key + auto-launch toggle), and
Whisper model download. On finish, persists the API key to keyring (if
non-empty), saves settings, syncs the autolaunch Run-key entry, and writes
the marker file so the wizard never shows again.

The model_downloader callable is injected for testability — production code
uses _default_model_downloader which triggers faster-whisper's HF download.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import keyring
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
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
from teams_transcriber.runtime import gpu_runtime
from teams_transcriber.ui.frameless import FramelessWindowMixin
from teams_transcriber.ui.title_bar import TitleBar

logger = logging.getLogger(__name__)

ModelDownloader = Callable[[Callable[[int], None]], None]
"""Callable(progress_callback)->None. progress_callback receives 0..100."""


def _default_model_downloader(progress: Callable[[int], None]) -> None:
    """Trigger faster-whisper to populate the HF cache for the configured model.

    Coarse progress only — faster-whisper doesn't expose download progress.
    """
    progress(10)
    from faster_whisper import WhisperModel

    WhisperModel(
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        device="cpu", compute_type="int8",
    )
    progress(100)


class FirstRunWizard(FramelessWindowMixin, QDialog):
    finished_ok = Signal()

    def __init__(
        self,
        *,
        settings: Settings,
        paths: AppPaths,
        model_downloader: ModelDownloader = _default_model_downloader,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Teams Transcriber")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setModal(True)
        self.setMinimumSize(520, 420)
        self._settings = settings
        self._paths = paths
        self._model_downloader = model_downloader

        frame = QFrame()
        frame.setObjectName("OuterFrame")
        shell = QVBoxLayout(self)
        shell.addWidget(frame)

        inner = QVBoxLayout(frame)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        self._title_bar = TitleBar(title="Welcome", controls=("close",))
        self._title_bar.close_requested.connect(self.reject)
        inner.addWidget(self._title_bar)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 16)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_welcome())
        self._stack.addWidget(self._build_setup())
        self._stack.addWidget(self._build_gpu_runtime())
        self._stack.addWidget(self._build_model_download())
        body_layout.addWidget(self._stack, 1)

        nav = QHBoxLayout()
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._back)
        self._next_btn = QPushButton("Next")
        self._next_btn.clicked.connect(self._next)
        nav.addStretch()
        nav.addWidget(self._back_btn)
        nav.addWidget(self._next_btn)
        body_layout.addLayout(nav)

        inner.addWidget(body, 1)

        self._init_frameless(frame, resizable=True, title_bar=self._title_bar,
                             shell_layout=shell)
        self._update_nav()

    # --- pages -----------------------------------------------------------

    def _build_welcome(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h2>Welcome to Teams Transcriber</h2>"))
        body = QLabel(
            "Teams Transcriber records your Microsoft Teams meetings, "
            "transcribes them locally on your GPU, and summarizes them "
            "with Claude. Press Next to set up."
        )
        body.setWordWrap(True)
        v.addWidget(body)
        v.addStretch()
        return w

    def _build_setup(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>Set up your account</h3>"))
        v.addWidget(QLabel(
            "Enter your Anthropic API key. You can skip this and add it "
            "later in Settings."
        ))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("sk-ant-...")
        v.addWidget(self.api_key_input)
        v.addWidget(QLabel(
            "<small>Get a key at https://console.anthropic.com/</small>",
        ))
        v.addSpacing(20)
        self.auto_launch_cb = QCheckBox("Start Teams Transcriber when I log on")
        self.auto_launch_cb.setChecked(True)
        v.addWidget(self.auto_launch_cb)
        v.addStretch()
        return w

    def _build_gpu_runtime(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>Download GPU runtime</h3>"))
        v.addWidget(QLabel(
            "Teams Transcriber uses NVIDIA's CUDA libraries for "
            "GPU-accelerated transcription (~700 MB). This is a "
            "one-time download."
        ))
        self.gpu_progress_bar = QProgressBar()
        self.gpu_progress_bar.setRange(0, 100)
        v.addWidget(self.gpu_progress_bar)
        self.gpu_progress_label = QLabel("Click Next to start the download.")
        self.gpu_progress_label.setWordWrap(True)
        v.addWidget(self.gpu_progress_label)
        v.addStretch()
        return w

    def _build_model_download(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>Download the Whisper model</h3>"))
        v.addWidget(QLabel(
            "Teams Transcriber uses a local speech-recognition model (~3 GB). "
            "This is a one-time download."
        ))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        v.addWidget(self.progress_bar)
        self.progress_label = QLabel("Click Next to start the download.")
        self.progress_label.setWordWrap(True)
        v.addWidget(self.progress_label)
        v.addStretch()
        return w

    # --- navigation ------------------------------------------------------

    def _update_nav(self) -> None:
        idx = self._stack.currentIndex()
        self._back_btn.setEnabled(idx > 0)
        self._next_btn.setText("Finish" if idx == self._stack.count() - 1 else "Next")

    def _next(self) -> None:
        idx = self._stack.currentIndex()
        if idx == self._stack.count() - 1:
            self._finish()
            return
        self._stack.setCurrentIndex(idx + 1)
        self._update_nav()
        new_idx = self._stack.currentIndex()
        if new_idx == 2:
            self._kick_gpu_runtime_download()
        elif new_idx == 3:
            self._kick_model_download()

    def _back(self) -> None:
        self._stack.setCurrentIndex(max(0, self._stack.currentIndex() - 1))
        self._update_nav()

    def _kick_model_download(self) -> None:
        self.progress_label.setText("Downloading model...")
        try:
            self._model_downloader(self._on_progress)
            self.progress_label.setText("Model ready.")
            self.progress_bar.setValue(100)
        except Exception as exc:
            logger.exception("model download failed")
            self.progress_label.setText(
                f"Model download failed: {exc}. You can retry later from Settings."
            )

    def _kick_gpu_runtime_download(self) -> None:
        runtime_base = self._paths.runtime_dir / "nvidia"
        if gpu_runtime.is_runtime_installed(runtime_base):
            self.gpu_progress_label.setText("GPU runtime already installed.")
            self.gpu_progress_bar.setValue(100)
            return
        self.gpu_progress_label.setText("Downloading GPU runtime...")
        try:
            self._download_gpu_runtime(runtime_base)
            self.gpu_progress_label.setText("GPU runtime ready.")
            self.gpu_progress_bar.setValue(100)
        except Exception as exc:
            logger.exception("GPU runtime download failed")
            self.gpu_progress_label.setText(
                f"GPU runtime download failed: {exc}. "
                "You can retry on next launch."
            )

    def _download_gpu_runtime(self, runtime_base) -> None:
        seen_packages: list[str] = []

        def progress(name: str, done: int, total: int) -> None:
            if name not in seen_packages:
                seen_packages.append(name)
            pct = int(100 * len(seen_packages) / max(1, len(gpu_runtime.REQUIRED_PACKAGES)))
            self.gpu_progress_bar.setValue(min(99, pct))
            self.gpu_progress_label.setText(f"Downloading {name}...")

        gpu_runtime.download_runtime(runtime_base, progress_callback=progress)

    def _on_progress(self, pct: int) -> None:
        self.progress_bar.setValue(pct)

    def _finish(self) -> None:
        key = self.api_key_input.text().strip()
        if key:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER_ANTHROPIC, key)

        self._settings._raw["general"]["auto_launch"] = self.auto_launch_cb.isChecked()
        save_settings(self._paths, self._settings)

        from teams_transcriber import autolaunch
        if self.auto_launch_cb.isChecked():
            autolaunch.enable()
        else:
            autolaunch.disable()

        self._paths.config_dir.mkdir(parents=True, exist_ok=True)
        self._paths.first_run_marker_path.write_text("ok\n", encoding="utf-8")

        self.finished_ok.emit()
        self.accept()
