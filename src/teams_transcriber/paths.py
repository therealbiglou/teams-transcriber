"""Resolves on-disk paths for app data, audio, models, logs, and config."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR_NAME = "TeamsTranscriber"


def _default_root() -> Path:
    """Return %LOCALAPPDATA%\\TeamsTranscriber, with sensible fallbacks for non-Windows test envs."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    # Fallback: ~/.local/share/TeamsTranscriber (developer machines, CI).
    return Path.home() / ".local" / "share" / APP_DIR_NAME


@dataclass(slots=True)
class AppPaths:
    """Standard locations for app-managed files. Override `root` for tests."""

    root: Path = field(default_factory=_default_root)

    @property
    def db_path(self) -> Path:
        return self.root / "teams_transcriber.db"

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def runtime_dir(self) -> Path:
        return self.root / "runtime"

    @property
    def first_run_marker_path(self) -> Path:
        return self.config_dir / ".first-run-complete"

    def ensure_dirs(self) -> None:
        """Create all managed directories. Safe to call repeatedly."""
        for d in (self.root, self.audio_dir, self.models_dir, self.logs_dir,
                  self.config_dir, self.runtime_dir):
            d.mkdir(parents=True, exist_ok=True)
