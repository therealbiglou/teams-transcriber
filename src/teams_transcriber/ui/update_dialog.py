"""Modal dialog: downloads the new installer and offers Restart."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.paths import AppPaths
from teams_transcriber.update_checker import (
    ReleaseInfo,
    UpdateCheckError,
    download_installer,
)

logger = logging.getLogger(__name__)


class _DownloadWorker(QObject):
    """Runs download_installer on a thread; signals progress + done to the GUI thread."""

    progress = Signal(int, int)   # done, total
    finished = Signal(str)        # error message; empty string = success

    def __init__(self, url: str, target: Path, expected_size: int) -> None:
        super().__init__()
        self._url = url
        self._target = target
        self._expected_size = expected_size

    def run(self) -> None:
        release = ReleaseInfo(
            tag="", version=(0, 0, 0), is_prerelease=False,
            installer_url=self._url, installer_size=self._expected_size,
            html_url="",
        )
        try:
            download_installer(
                release, self._target,
                progress_callback=lambda d, t: self.progress.emit(d, t),
            )
            self.finished.emit("")
        except UpdateCheckError as exc:
            logger.exception("update download failed")
            self.finished.emit(str(exc))


class UpdateDialog(QDialog):
    """Download progress + 'Restart now' / 'Later' prompt after download."""

    def __init__(
        self,
        *,
        version: str,
        download_url: str,
        paths: AppPaths,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Update to {version}")
        self.setMinimumWidth(440)
        self._installer_path = paths.root / "update" / f"TeamsTranscriberSetup-{version.lstrip('v')}.exe"

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(QLabel(f"<b>Downloading update {version}…</b>"))

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        layout.addWidget(self._progress_bar)

        self._status_label = QLabel("Starting download…")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._button_row = QHBoxLayout()
        self._button_row.addStretch(1)
        layout.addLayout(self._button_row)

        # Kick off download on a worker thread.
        self._worker = _DownloadWorker(
            url=download_url, target=self._installer_path, expected_size=0,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        threading.Thread(target=self._worker.run, daemon=True).start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            pct = int(100 * done / total)
            self._progress_bar.setValue(pct)
            self._status_label.setText(
                f"Downloaded {done // (1024 * 1024)} MB of {total // (1024 * 1024)} MB ({pct}%)"
            )
        else:
            self._status_label.setText(f"Downloaded {done // (1024 * 1024)} MB")

    def _on_finished(self, error: str) -> None:
        if error:
            self._status_label.setText(f"Download failed: {error}")
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(self.reject)
            self._button_row.addWidget(close_btn)
            return

        self._status_label.setText(
            "Update downloaded. Restart Teams Transcriber now to install?"
        )
        self._progress_bar.setValue(100)
        restart_btn = QPushButton("Restart now")
        restart_btn.setProperty("role", "primary")
        restart_btn.clicked.connect(self._launch_installer_and_quit)
        later_btn = QPushButton("Later")
        later_btn.setProperty("role", "secondary")
        later_btn.clicked.connect(self.accept)
        self._button_row.addWidget(later_btn)
        self._button_row.addWidget(restart_btn)

    def _launch_installer_and_quit(self) -> None:
        try:
            subprocess.Popen(
                [
                    str(self._installer_path),
                    "/SILENT",
                    "/CLOSEAPPLICATIONS",
                    "/RESTARTAPPLICATIONS",
                ],
                creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
            )
        except OSError as exc:
            self._status_label.setText(
                f"Could not launch installer: {exc}. "
                f"Installer is at: {self._installer_path}"
            )
            return
        sys.exit(0)
