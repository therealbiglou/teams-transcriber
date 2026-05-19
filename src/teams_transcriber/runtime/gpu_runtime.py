"""Download + register NVIDIA CUDA runtime DLLs on first launch.

faster-whisper / CTranslate2 require cuBLAS, cuDNN, and NVRTC at GPU
inference time. To keep the installer small, those wheels are NOT shipped
inside the PyInstaller bundle — instead the first-run wizard downloads
them from PyPI into a per-user cache (analogous to the Whisper model
download). Subsequent launches just re-register the cached DLLs.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class GpuRuntimeError(RuntimeError):
    """Raised when the runtime can't be downloaded, verified, or extracted."""


REQUIRED_PACKAGES: list[tuple[str, str]] = [
    ("nvidia-cublas-cu12",      "12.4.5.8"),
    ("nvidia-cudnn-cu12",       "9.1.0.70"),
    ("nvidia-cuda-nvrtc-cu12",  "12.4.127"),
]


def package_dir(runtime_base: Path, name: str, version: str) -> Path:
    """Per-package, per-version directory under the runtime cache."""
    return runtime_base / f"{name}-{version}"


def is_runtime_installed(runtime_base: Path) -> bool:
    """True iff every REQUIRED_PACKAGES version dir exists and has DLLs."""
    for name, version in REQUIRED_PACKAGES:
        pkg = package_dir(runtime_base, name, version)
        if not pkg.is_dir():
            return False
        if not any(pkg.rglob("*.dll")):
            return False
    return True


def register_runtime(runtime_base: Path) -> bool:
    """Add every bin/ dir in the runtime to os.add_dll_directory + PATH.

    Returns True if registration succeeded, False if runtime not installed.
    Safe to call repeatedly.
    """
    if not is_runtime_installed(runtime_base):
        return False
    added: list[str] = []
    for name, version in REQUIRED_PACKAGES:
        pkg = package_dir(runtime_base, name, version)
        for bin_dir in pkg.rglob("bin"):
            if bin_dir.is_dir() and any(bin_dir.glob("*.dll")):
                path_str = str(bin_dir)
                with contextlib.suppress(OSError, AttributeError):
                    os.add_dll_directory(path_str)
                added.append(path_str)
    if added:
        existing = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join([*added, existing])
    return True
