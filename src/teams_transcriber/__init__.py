"""Teams Transcriber — auto-record and summarize Teams meetings."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

__version__ = "0.4.1"


def _nvidia_root() -> Path | None:
    """Return the directory containing pip-installed NVIDIA runtime wheels.

    Source mode: `<venv>/Lib/site-packages/nvidia/`.
    Frozen mode: `<_MEIPASS>/nvidia/` (PyInstaller-bundled wheels).
    Returns None if the directory doesn't exist (CPU-only use, tests, etc.).
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidate = base / "nvidia"
    else:
        candidate = Path(sys.executable).parent.parent / "Lib" / "site-packages" / "nvidia"
    return candidate if candidate.is_dir() else None


def _register_nvidia_dll_dirs() -> None:
    """On Windows, add the pip-installed NVIDIA runtime DLL dirs to the loader path.

    `ctranslate2` (used by faster-whisper) links against `cublas64_12.dll`, `cudnn64_9.dll`,
    and `nvrtc64_*.dll`. The `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` PyPI wheels ship
    these under `<venv>/Lib/site-packages/nvidia/<lib>/bin/`, but Windows' restricted DLL
    search doesn't look there. `os.add_dll_directory` adds them explicitly.

    Silently does nothing on non-Windows or when the wheels aren't installed (CPU-only
    use case, tests with mocked Whisper, etc.).
    """
    if not sys.platform.startswith("win"):
        return
    nvidia_root = _nvidia_root()
    if nvidia_root is None:
        return
    added: list[str] = []
    for bin_dir in nvidia_root.rglob("bin"):
        if bin_dir.is_dir() and any(bin_dir.glob("*.dll")):
            path_str = str(bin_dir)
            with contextlib.suppress(OSError, AttributeError):
                os.add_dll_directory(path_str)
            added.append(path_str)
    # Belt-and-suspenders: ctranslate2's DLL loader respects PATH on Windows.
    if added:
        os.environ["PATH"] = os.pathsep.join([*added, os.environ.get("PATH", "")])


def _register_downloaded_gpu_runtime() -> None:
    """Register DLLs from the per-user GPU runtime cache (Phase 7).

    When the installer doesn't ship NVIDIA libs, they live in
    %LOCALAPPDATA%\\TeamsTranscriber\\runtime\\nvidia\\ after the first-run
    wizard. Calling this is a no-op when the runtime isn't yet installed.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        from teams_transcriber.paths import AppPaths
        from teams_transcriber.runtime.gpu_runtime import register_runtime
    except Exception:
        return
    try:
        paths = AppPaths()
        register_runtime(paths.runtime_dir / "nvidia")
    except Exception:
        pass


_register_nvidia_dll_dirs()
_register_downloaded_gpu_runtime()
