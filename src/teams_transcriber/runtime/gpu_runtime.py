"""Download + register NVIDIA CUDA runtime DLLs on first launch.

faster-whisper / CTranslate2 require cuBLAS, cuDNN, and NVRTC at GPU
inference time. To keep the installer small, those wheels are NOT shipped
inside the PyInstaller bundle — instead the first-run wizard downloads
them from PyPI into a per-user cache (analogous to the Whisper model
download). Subsequent launches just re-register the cached DLLs.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
import urllib.request
import zipfile
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


PYPI_JSON_URL = "https://pypi.org/pypi/{name}/{version}/json"


def _fetch_wheel_metadata(name: str, version: str) -> dict:
    """Return {url, sha256, filename} for the matching wheel on PyPI."""
    url = PYPI_JSON_URL.format(name=name, version=version)
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())
    urls = data.get("urls", [])
    if not urls:
        raise GpuRuntimeError(f"no wheels on PyPI for {name}=={version}")
    for entry in urls:
        if entry.get("filename", "").endswith(".whl"):
            return {
                "url": entry["url"],
                "sha256": entry["digests"]["sha256"],
                "filename": entry["filename"],
            }
    raise GpuRuntimeError(f"no .whl entry for {name}=={version}")


def _download_bytes(url: str) -> bytes:
    """Download the URL into memory. Returns the body."""
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def download_runtime(
    runtime_base: Path,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> None:
    """Download + extract every REQUIRED_PACKAGES wheel into `runtime_base`.

    Packages already installed are skipped. Failures raise GpuRuntimeError.
    Progress callback receives (package_name, bytes_done, bytes_total).
    """
    runtime_base.mkdir(parents=True, exist_ok=True)

    for name, version in REQUIRED_PACKAGES:
        target = package_dir(runtime_base, name, version)
        if target.is_dir() and any(target.rglob("*.dll")):
            logger.info("gpu_runtime: %s==%s already installed, skipping", name, version)
            continue

        try:
            metadata = _fetch_wheel_metadata(name, version)
            wheel_url = metadata["url"]
            expected_sha = metadata["sha256"]

            if progress_callback is not None:
                progress_callback(name, 0, 0)

            wheel_bytes = _download_bytes(wheel_url)

            actual_sha = hashlib.sha256(wheel_bytes).hexdigest()
            if actual_sha != expected_sha:
                raise GpuRuntimeError(
                    f"SHA256 mismatch for {name}=={version}: "
                    f"expected {expected_sha}, got {actual_sha}",
                )

            if progress_callback is not None:
                progress_callback(name, len(wheel_bytes), len(wheel_bytes))

            with tempfile.TemporaryDirectory(dir=runtime_base) as tmp:
                tmp_path_dir = Path(tmp)
                wheel_path = tmp_path_dir / "wheel.zip"
                wheel_path.write_bytes(wheel_bytes)
                extract_to = tmp_path_dir / "extracted"
                extract_to.mkdir()
                with zipfile.ZipFile(wheel_path) as zf:
                    zf.extractall(extract_to)
                target.mkdir(parents=True, exist_ok=True)
                for item in extract_to.iterdir():
                    item.rename(target / item.name)

            logger.info("gpu_runtime: installed %s==%s into %s", name, version, target)
        except GpuRuntimeError:
            raise
        except Exception as exc:
            raise GpuRuntimeError(
                f"failed to install {name}=={version}: {exc}",
            ) from exc
