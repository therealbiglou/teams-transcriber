# Phase 7 — Decoupled GPU Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip NVIDIA's CUDA runtime DLLs out of the PyInstaller bundle. The app downloads them from PyPI on first launch into `%LOCALAPPDATA%\TeamsTranscriber\runtime\nvidia\<pkg>-<ver>\`, the same way the Whisper model already downloads. Installer drops from ~995 MB to ~250 MB; updates only redeliver the small bundle.

**Architecture:** A new `runtime/gpu_runtime.py` module pulls each pinned NVIDIA wheel from PyPI's JSON API, verifies SHA256, extracts the zip into the per-user runtime cache, and adds the resulting `bin/` directories to `os.add_dll_directory` + `PATH`. The first-run wizard gains a new page between API key entry and Whisper-model download. `__main__.py` checks for the runtime before any code that imports `ctranslate2` / `faster_whisper`. The PyInstaller spec drops the `cuda_binaries` collection.

**Tech Stack:** Python 3.11 stdlib only (`urllib.request`, `zipfile`, `hashlib`) for the runtime fetcher — no new dependencies. PySide6 for the wizard page. PyInstaller for the build change.

---

## File Structure

**New files:**

- `src/teams_transcriber/runtime/__init__.py` — empty package marker.
- `src/teams_transcriber/runtime/gpu_runtime.py` — download + verify + extract + register logic.
- `tests/runtime/__init__.py` — empty.
- `tests/runtime/test_gpu_runtime.py` — unit tests with all HTTP and filesystem operations mocked or scoped to `tmp_path`.
- `docs/superpowers/checklists/2026-05-19-phase-7-verification.md` — manual verification.

**Modified files:**

- `src/teams_transcriber/paths.py` — add `runtime_dir` property.
- `src/teams_transcriber/__init__.py` — register runtime cache paths in addition to the bundle path.
- `src/teams_transcriber/__main__.py` — bootstrap check before importing UI / pipeline (which touch ctranslate2).
- `src/teams_transcriber/ui/first_run_wizard.py` — insert GPU runtime page; trigger it conditionally.
- `teams_transcriber.spec` — clear `cuda_binaries`.

---

## Note on running tests

`uv` is not on PATH. Use:

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest ...
```

---

## Task 1: `AppPaths.runtime_dir`

**Files:**
- Modify: `src/teams_transcriber/paths.py`
- Test: `tests/storage/test_paths.py`

- [ ] **Step 1.1: Write failing test**

Append to `tests/storage/test_paths.py`:

```python
def test_runtime_dir_under_root() -> None:
    """runtime_dir is under root and ensure_dirs creates it."""
    import tempfile
    from pathlib import Path
    from teams_transcriber.paths import AppPaths

    with tempfile.TemporaryDirectory() as tmp:
        p = AppPaths(root=Path(tmp))
        assert p.runtime_dir == p.root / "runtime"
        p.ensure_dirs()
        assert p.runtime_dir.is_dir()
```

- [ ] **Step 1.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/storage/test_paths.py -k runtime_dir -v
```

- [ ] **Step 1.3: Implement**

In `src/teams_transcriber/paths.py`, add a `runtime_dir` property to `AppPaths` (analogous to existing `audio_dir`):

```python
    @property
    def runtime_dir(self) -> Path:
        return self.root / "runtime"
```

And add it to `ensure_dirs`:

```python
    def ensure_dirs(self) -> None:
        for d in (self.root, self.audio_dir, self.models_dir, self.logs_dir,
                  self.config_dir, self.runtime_dir):
            d.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 1.4: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/storage/test_paths.py -v
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest
```

- [ ] **Step 1.5: Commit**

```powershell
git add src/teams_transcriber/paths.py tests/storage/test_paths.py
git commit -m "feat(paths): add AppPaths.runtime_dir for downloaded GPU runtime cache"
```

---

## Task 2: `gpu_runtime.py` — version constants, install detection, register

**Files:**
- Create: `src/teams_transcriber/runtime/__init__.py` (empty)
- Create: `src/teams_transcriber/runtime/gpu_runtime.py`
- Create: `tests/runtime/__init__.py` (empty)
- Create: `tests/runtime/test_gpu_runtime.py`

This task ships the read-only parts of the runtime module — `REQUIRED_PACKAGES`, `is_runtime_installed`, `register_runtime`. The download logic comes in Task 3.

- [ ] **Step 2.1: Create the empty package markers**

```powershell
New-Item -ItemType File -Path src/teams_transcriber/runtime/__init__.py -Force
New-Item -ItemType File -Path tests/runtime/__init__.py -Force
```

- [ ] **Step 2.2: Write failing tests**

Create `tests/runtime/test_gpu_runtime.py`:

```python
"""Tests for the GPU runtime download + registration module."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def test_required_packages_pinned() -> None:
    """REQUIRED_PACKAGES is a list of (name, version) tuples, all pinned."""
    from teams_transcriber.runtime.gpu_runtime import REQUIRED_PACKAGES

    assert len(REQUIRED_PACKAGES) > 0
    for entry in REQUIRED_PACKAGES:
        name, version = entry
        assert name.startswith("nvidia-")
        # Version is a strict dotted version, not a range / wildcard.
        assert version[0].isdigit()
        assert " " not in version
        assert "*" not in version


def test_package_dir_under_runtime_base(tmp_path) -> None:
    """package_dir() returns <runtime_base>/<name>-<version>/."""
    from teams_transcriber.runtime.gpu_runtime import package_dir

    d = package_dir(tmp_path, "nvidia-cublas-cu12", "12.4.5.8")
    assert d == tmp_path / "nvidia-cublas-cu12-12.4.5.8"


def test_is_runtime_installed_false_when_dirs_missing(tmp_path) -> None:
    from teams_transcriber.runtime.gpu_runtime import is_runtime_installed

    assert is_runtime_installed(tmp_path) is False


def test_is_runtime_installed_true_when_all_dirs_have_dlls(tmp_path) -> None:
    """When each package's version dir contains at least one .dll under bin/,
    the runtime is considered installed."""
    from teams_transcriber.runtime.gpu_runtime import (
        REQUIRED_PACKAGES,
        is_runtime_installed,
        package_dir,
    )

    for name, version in REQUIRED_PACKAGES:
        bin_dir = package_dir(tmp_path, name, version) / "nvidia" / name.replace("nvidia-", "").replace("-cu12", "") / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "dummy.dll").write_bytes(b"\x00" * 32)

    assert is_runtime_installed(tmp_path) is True


def test_register_runtime_no_op_when_not_installed(tmp_path, monkeypatch) -> None:
    """register_runtime returns False when runtime isn't installed; doesn't touch PATH."""
    from teams_transcriber.runtime import gpu_runtime

    captured: list[str] = []
    monkeypatch.setattr(os, "add_dll_directory", lambda p: captured.append(p))
    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", original_path)

    assert gpu_runtime.register_runtime(tmp_path) is False
    assert captured == []
    assert os.environ.get("PATH", "") == original_path


def test_register_runtime_adds_bin_dirs_when_installed(tmp_path, monkeypatch) -> None:
    """When runtime is installed, register_runtime adds every bin/ dir to add_dll_directory + PATH."""
    from teams_transcriber.runtime import gpu_runtime

    for name, version in gpu_runtime.REQUIRED_PACKAGES:
        bin_dir = gpu_runtime.package_dir(tmp_path, name, version) / "nvidia" / name.replace("nvidia-", "").replace("-cu12", "") / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "fake.dll").write_bytes(b"\x00" * 32)

    captured: list[str] = []
    monkeypatch.setattr(os, "add_dll_directory", lambda p: captured.append(p))
    monkeypatch.setenv("PATH", "")

    assert gpu_runtime.register_runtime(tmp_path) is True
    assert len(captured) == len(gpu_runtime.REQUIRED_PACKAGES)
    new_path = os.environ.get("PATH", "")
    for added in captured:
        assert added in new_path
```

- [ ] **Step 2.3: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/runtime -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 2.4: Implement**

Create `src/teams_transcriber/runtime/gpu_runtime.py`:

```python
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
import logging
import os
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class GpuRuntimeError(RuntimeError):
    """Raised when the runtime can't be downloaded, verified, or extracted."""


# Pinned NVIDIA wheel versions known to be compatible with the
# ctranslate2 / faster-whisper versions in pyproject.toml.
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
        # Look for any .dll under the unpacked tree (avoids depending on a
        # specific internal layout). Wheels for nvidia-* unpack into
        # nvidia/<subpkg>/bin/*.dll.
        if not any(pkg.rglob("*.dll")):
            return False
    return True


def register_runtime(runtime_base: Path) -> bool:
    """Add every bin/ dir in the runtime to os.add_dll_directory + PATH.

    Returns True if registration succeeded (i.e., runtime is installed and
    every bin/ dir was added), False if runtime is not installed.

    Safe to call repeatedly on the same paths; os.add_dll_directory silently
    de-dupes and the PATH prepend is idempotent for our purposes.
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
```

- [ ] **Step 2.5: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/runtime -v
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest
```

- [ ] **Step 2.6: Commit**

```powershell
git add src/teams_transcriber/runtime tests/runtime
git commit -m "feat(runtime): add gpu_runtime module — is_runtime_installed + register_runtime"
```

---

## Task 3: `gpu_runtime.download_runtime` — PyPI fetch + extract

**Files:**
- Modify: `src/teams_transcriber/runtime/gpu_runtime.py`
- Modify: `tests/runtime/test_gpu_runtime.py`

- [ ] **Step 3.1: Write failing tests**

Append to `tests/runtime/test_gpu_runtime.py`:

```python
def test_download_runtime_calls_per_package_with_mocked_fetch(tmp_path, monkeypatch) -> None:
    """download_runtime processes each REQUIRED_PACKAGES entry sequentially,
    invokes progress callback, and extracts the wheel into the right dir."""
    from teams_transcriber.runtime import gpu_runtime

    # Build a tiny fake wheel (a zip file with one nvidia/foo/bin/fake.dll).
    def _build_fake_wheel(path: Path, pkg_subdir: str) -> bytes:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(f"nvidia/{pkg_subdir}/bin/fake.dll", b"\x00\x00\x00")
        return path.read_bytes()

    fake_wheels: dict[str, bytes] = {}
    for name, version in gpu_runtime.REQUIRED_PACKAGES:
        wheel_bytes = _build_fake_wheel(
            tmp_path / f"{name}-{version}.whl",
            name.replace("nvidia-", "").replace("-cu12", ""),
        )
        fake_wheels[name] = wheel_bytes

    fetched: list[tuple[str, str]] = []

    def fake_fetch_metadata(name: str, version: str):
        fetched.append((name, version))
        return {
            "url": f"https://example.com/{name}.whl",
            "sha256": hashlib.sha256(fake_wheels[name]).hexdigest(),
        }

    def fake_download(url: str) -> bytes:
        # Map URL back to a package name by suffix.
        for name in fake_wheels:
            if url.endswith(f"/{name}.whl"):
                return fake_wheels[name]
        raise ValueError(f"unexpected URL {url}")

    monkeypatch.setattr(gpu_runtime, "_fetch_wheel_metadata", fake_fetch_metadata)
    monkeypatch.setattr(gpu_runtime, "_download_bytes", fake_download)

    progress_calls: list[tuple[str, int, int]] = []
    def progress(name: str, done: int, total: int) -> None:
        progress_calls.append((name, done, total))

    gpu_runtime.download_runtime(tmp_path / "runtime", progress_callback=progress)

    assert [n for n, _ in fetched] == [n for n, _ in gpu_runtime.REQUIRED_PACKAGES]
    assert gpu_runtime.is_runtime_installed(tmp_path / "runtime") is True
    # Progress callback was invoked at least once per package.
    seen = {name for name, _, _ in progress_calls}
    assert seen == {name for name, _ in gpu_runtime.REQUIRED_PACKAGES}


def test_download_runtime_sha256_mismatch_raises(tmp_path, monkeypatch) -> None:
    from teams_transcriber.runtime import gpu_runtime

    name, version = gpu_runtime.REQUIRED_PACKAGES[0]

    def fake_metadata(n, v):
        return {"url": "https://example.com/x.whl", "sha256": "0" * 64}

    monkeypatch.setattr(gpu_runtime, "_fetch_wheel_metadata", fake_metadata)
    monkeypatch.setattr(gpu_runtime, "_download_bytes", lambda url: b"corrupt content")

    with pytest.raises(gpu_runtime.GpuRuntimeError):
        gpu_runtime.download_runtime(tmp_path / "runtime")


def test_download_runtime_already_installed_short_circuits(tmp_path, monkeypatch) -> None:
    """If a package's version dir already exists with .dlls, skip its download."""
    from teams_transcriber.runtime import gpu_runtime

    # Pre-seed all packages as installed.
    for name, version in gpu_runtime.REQUIRED_PACKAGES:
        bin_dir = gpu_runtime.package_dir(tmp_path, name, version) / "nvidia" / "x" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "fake.dll").write_bytes(b"\x00")

    fetched: list[str] = []
    monkeypatch.setattr(
        gpu_runtime, "_fetch_wheel_metadata",
        lambda n, v: fetched.append(n) or {"url": "", "sha256": ""},
    )

    gpu_runtime.download_runtime(tmp_path)
    assert fetched == []  # no fetches; all skipped
```

- [ ] **Step 3.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/runtime -v
```

- [ ] **Step 3.3: Implement**

Add to `src/teams_transcriber/runtime/gpu_runtime.py`:

```python
import json


PYPI_JSON_URL = "https://pypi.org/pypi/{name}/{version}/json"


def _fetch_wheel_metadata(name: str, version: str) -> dict:
    """Return {url, sha256} for the matching wheel on PyPI.

    Resolves to the cp311-win_amd64 wheel (or the first wheel if no
    platform-specific match — nvidia-* wheels are typically py3-none-manylinux,
    but the cu12 variants are platform-aware).
    """
    url = PYPI_JSON_URL.format(name=name, version=version)
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())
    urls = data.get("urls", [])
    if not urls:
        raise GpuRuntimeError(f"no wheels on PyPI for {name}=={version}")
    # Pick the first .whl entry — nvidia-cu12 packages publish a single wheel
    # per version.
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

    Packages already installed (i.e. their version dir contains DLLs) are
    skipped. Network or extraction failures raise GpuRuntimeError.

    The progress callback receives (package_name, bytes_done, bytes_total).
    bytes_total is the wheel's content length; bytes_done is monotonically
    nondecreasing per package.
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

            # Extract into a temp dir, then atomic-rename to the target.
            # This avoids leaving a half-extracted dir on disk if extract fails.
            with tempfile.TemporaryDirectory(dir=runtime_base) as tmp:
                tmp_path = Path(tmp)
                wheel_path = tmp_path / "wheel.zip"
                wheel_path.write_bytes(wheel_bytes)
                extract_to = tmp_path / "extracted"
                extract_to.mkdir()
                with zipfile.ZipFile(wheel_path) as zf:
                    zf.extractall(extract_to)
                # Move the extracted contents into the final target.
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
```

The `pytest` and `zipfile` / `hashlib` imports needed by tests should already be in the test file's import section.

- [ ] **Step 3.4: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/runtime -v
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest
```

- [ ] **Step 3.5: Commit**

```powershell
git add src/teams_transcriber/runtime/gpu_runtime.py tests/runtime/test_gpu_runtime.py
git commit -m "feat(runtime): add download_runtime — PyPI JSON API + SHA256 + atomic extract"
```

---

## Task 4: `__init__.py` — also register the runtime cache

**Files:**
- Modify: `src/teams_transcriber/__init__.py`
- Test: existing tests should continue to pass; no new tests needed (the
  behavior is "additive" — the bundled-nvidia path still works on dev machines).

- [ ] **Step 4.1: Implement**

In `src/teams_transcriber/__init__.py`, add after the existing
`_register_nvidia_dll_dirs()` call:

```python
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
    except Exception:  # noqa: BLE001
        return
    try:
        paths = AppPaths()
        register_runtime(paths.runtime_dir / "nvidia")
    except Exception:  # noqa: BLE001
        # Stay silent — first-run wizard will surface real errors.
        pass


_register_nvidia_dll_dirs()
_register_downloaded_gpu_runtime()
```

(Both paths are tried; whichever happens to be populated will work.)

- [ ] **Step 4.2: Run full suite**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest
```

All tests should still pass — the new function is a no-op without a populated runtime dir.

- [ ] **Step 4.3: Commit**

```powershell
git add src/teams_transcriber/__init__.py
git commit -m "feat(runtime): __init__ also registers DLLs from the downloaded GPU runtime cache"
```

---

## Task 5: First-run wizard — GPU runtime page

**Files:**
- Modify: `src/teams_transcriber/ui/first_run_wizard.py`
- Test: `tests/ui/test_first_run_wizard.py`

- [ ] **Step 5.1: Write failing test**

Append to `tests/ui/test_first_run_wizard.py`:

```python
def test_wizard_skips_gpu_runtime_page_when_already_installed(qapp, qtbot, paths, monkeypatch) -> None:
    """If is_runtime_installed returns True, the wizard doesn't kick off a download."""
    from teams_transcriber.config import load_settings
    from teams_transcriber.ui.first_run_wizard import FirstRunWizard

    paths.ensure_dirs()
    settings = load_settings(paths)

    download_calls: list[str] = []

    def fake_download(runtime_base, progress_callback=None):
        download_calls.append("invoked")

    monkeypatch.setattr(
        "teams_transcriber.runtime.gpu_runtime.is_runtime_installed",
        lambda _base: True,
    )
    monkeypatch.setattr(
        "teams_transcriber.runtime.gpu_runtime.download_runtime",
        fake_download,
    )

    wiz = FirstRunWizard(
        settings=settings, paths=paths,
        model_downloader=lambda progress: progress(100),
    )
    # Walk welcome → setup → gpu runtime → model
    wiz._next()  # welcome → setup
    wiz._next()  # setup → gpu runtime (should auto-skip and continue)
    # We expect to land on the model page (last page) without invoking download_runtime.
    assert download_calls == []


def test_wizard_kicks_off_gpu_runtime_download_when_not_installed(
    qapp, qtbot, paths, monkeypatch,
) -> None:
    from teams_transcriber.config import load_settings
    from teams_transcriber.ui.first_run_wizard import FirstRunWizard

    paths.ensure_dirs()
    settings = load_settings(paths)

    download_calls: list[str] = []

    def fake_download(runtime_base, progress_callback=None):
        download_calls.append("invoked")
        if progress_callback:
            progress_callback("nvidia-cublas-cu12", 0, 100)
            progress_callback("nvidia-cublas-cu12", 100, 100)

    monkeypatch.setattr(
        "teams_transcriber.runtime.gpu_runtime.is_runtime_installed",
        lambda _base: False,
    )
    monkeypatch.setattr(
        "teams_transcriber.runtime.gpu_runtime.download_runtime",
        fake_download,
    )

    wiz = FirstRunWizard(
        settings=settings, paths=paths,
        model_downloader=lambda progress: progress(100),
    )
    wiz._next()  # welcome → setup
    wiz._next()  # setup → gpu runtime page → auto-kick
    assert download_calls == ["invoked"]
```

- [ ] **Step 5.2: Run, expect fail**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/ui/test_first_run_wizard.py -k gpu_runtime -v
```

- [ ] **Step 5.3: Implement**

In `src/teams_transcriber/ui/first_run_wizard.py`:

1. Add import:

```python
from teams_transcriber.runtime import gpu_runtime
```

2. Add a new page builder method (place it between `_build_setup` and `_build_model_download`):

```python
    def _build_gpu_runtime(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>Download GPU runtime</h3>"))
        v.addWidget(QLabel(
            "Teams Transcriber uses NVIDIA's CUDA libraries for GPU-accelerated "
            "transcription (~700 MB). This is a one-time download."
        ))
        self.gpu_progress_bar = QProgressBar()
        self.gpu_progress_bar.setRange(0, 100)
        v.addWidget(self.gpu_progress_bar)
        self.gpu_progress_label = QLabel("Click Next to start the download.")
        self.gpu_progress_label.setWordWrap(True)
        v.addWidget(self.gpu_progress_label)
        v.addStretch()
        return w
```

3. Add the page to the stack in `__init__` (between setup and model_download):

```python
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_welcome())
        self._stack.addWidget(self._build_setup())
        self._stack.addWidget(self._build_gpu_runtime())
        self._stack.addWidget(self._build_model_download())
```

4. Update `_next` to handle the new page index. The current code triggers
   the model download when `currentIndex() == 2`; we need to bump that to 3
   and add the GPU runtime trigger at index 2:

```python
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
```

5. Add the kicker method:

```python
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
        """Wrap so tests can monkeypatch the inner call. The actual download
        runs synchronously on the GUI thread for v1 — wheels are large but
        the wizard is the only window onscreen."""
        seen_packages: list[str] = []

        def progress(name: str, done: int, total: int) -> None:
            if name not in seen_packages:
                seen_packages.append(name)
            # Coarse: each package = 1/N of total progress.
            pct = int(100 * (len(seen_packages) - (0 if total > done else 0)) / max(1, len(gpu_runtime.REQUIRED_PACKAGES)))
            self.gpu_progress_bar.setValue(min(99, pct))
            self.gpu_progress_label.setText(f"Downloading {name}...")

        gpu_runtime.download_runtime(runtime_base, progress_callback=progress)
```

- [ ] **Step 5.4: Run, expect pass**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest tests/ui/test_first_run_wizard.py -v
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest
```

- [ ] **Step 5.5: Commit**

```powershell
git add src/teams_transcriber/ui/first_run_wizard.py tests/ui/test_first_run_wizard.py
git commit -m "feat(wizard): add GPU runtime download page between API key and Whisper model"
```

---

## Task 6: `__main__.py` — runtime check before app start

**Files:**
- Modify: `src/teams_transcriber/__main__.py`
- Test: skipped — the entry point is exercised in manual verification.

- [ ] **Step 6.1: Inspect the existing entry point**

Read `src/teams_transcriber/__main__.py`. It currently dispatches argv to
either the UI or CLI (`serve`, `retry-summary`).

- [ ] **Step 6.2: Add the runtime check**

At the top of `__main__.py`'s `main()` (or whatever the top-level function
is), before any imports of `pipeline`, `ui.app`, etc., add a runtime
bootstrap check:

```python
def _bootstrap_gpu_runtime() -> bool:
    """If the runtime isn't installed, launch the wizard only (UI mode).

    Returns True if the runtime is installed (or just-now installed), False
    if the caller should exit (CLI mode without runtime, or wizard cancelled).
    """
    import sys
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.runtime.gpu_runtime import (
        is_runtime_installed,
        register_runtime,
    )

    paths = AppPaths()
    paths.ensure_dirs()
    runtime_base = paths.runtime_dir / "nvidia"
    if is_runtime_installed(runtime_base):
        register_runtime(runtime_base)
        return True

    # No runtime. UI mode: let the wizard handle it. CLI mode: exit cleanly.
    if len(sys.argv) > 1 and sys.argv[1] in {"serve", "retry-summary", "smoke-test"}:
        print(
            "GPU runtime not installed. Launch the GUI once to set it up "
            "(it'll download ~700 MB of NVIDIA libraries).",
            file=sys.stderr,
        )
        return False
    return True  # UI mode falls through; wizard handles missing runtime.
```

Call it at the start of `main()`:

```python
def main() -> int:
    if not _bootstrap_gpu_runtime():
        return 2
    # ... existing dispatch logic ...
```

- [ ] **Step 6.3: Run full suite**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest
```

All tests should still pass — the bootstrap is conditional and a no-op
when no runtime cache exists yet (the wizard takes over).

- [ ] **Step 6.4: Commit**

```powershell
git add src/teams_transcriber/__main__.py
git commit -m "feat(entry): bootstrap GPU runtime check before app start"
```

---

## Task 7: PyInstaller spec — drop nvidia/* from bundle

**Files:**
- Modify: `teams_transcriber.spec`
- Test: build + smoke-test (not a unit test).

- [ ] **Step 7.1: Update the spec**

In `teams_transcriber.spec`:

1. Remove the entire `cuda_binaries` collection block:

```python
# DELETE these lines:
NVIDIA_ROOT = SITE_PACKAGES / "nvidia"
cuda_binaries = []
if NVIDIA_ROOT.is_dir():
    for dll in NVIDIA_ROOT.rglob("*.dll"):
        rel = dll.relative_to(NVIDIA_ROOT)
        cuda_binaries.append((str(dll), str(Path("nvidia") / rel.parent)))
```

2. Remove `cuda_binaries` from the `binaries=` line of `Analysis(...)`. Change:

```python
    binaries=av_binaries + ct2_binaries + sd_binaries + fw_binaries + cuda_binaries,
```

to:

```python
    binaries=av_binaries + ct2_binaries + sd_binaries + fw_binaries,
```

3. Additionally, defensively filter out any `nvidia/*` that other `collect_all`
   calls may have picked up:

```python
def _is_nvidia_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    return p.startswith("nvidia/") or "/nvidia/" in p
```

Place that helper near the top of the spec file. Then, after the
`Analysis(...)` block:

```python
a.binaries = [b for b in a.binaries if not _is_nvidia_path(b[0])]
a.datas    = [d for d in a.datas    if not _is_nvidia_path(d[0])]
```

- [ ] **Step 7.2: Verify the test suite still passes** (no NVIDIA libs in
the test/runtime path)

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest
```

- [ ] **Step 7.3: Commit**

```powershell
git add teams_transcriber.spec
git commit -m "build(installer): drop NVIDIA wheels from bundle — downloaded at first launch instead"
```

---

## Task 8: Manual verification checklist + bump + build + release

**Files:**
- Create: `docs/superpowers/checklists/2026-05-19-phase-7-verification.md`
- Modify: `pyproject.toml` (version bump 0.3.0 → 0.4.0)
- Build + release.

- [ ] **Step 8.1: Write the checklist**

Create `docs/superpowers/checklists/2026-05-19-phase-7-verification.md`:

```markdown
# Phase 7 Manual Verification

## Build

- [ ] `dist/TeamsTranscriberSetup-0.4.0.exe` exists.
- [ ] Installer size is ~250 MB (compare to Phase 6's 995 MB).

## Fresh-machine install path

On a Windows machine that has never had the app installed and has no cached
GPU runtime:

- [ ] Run the installer. Installs cleanly to `%LOCALAPPDATA%\Programs\TeamsTranscriber\`.
- [ ] Launch the app. First-run wizard opens.
- [ ] Walk: Welcome → API key → **GPU runtime** page.
- [ ] Click Next on the GPU runtime page. Download begins (~700 MB total).
      Progress bar advances; label updates with the current package.
- [ ] Download completes. Next.
- [ ] Whisper model page downloads ~3 GB. Next.
- [ ] Wizard finishes. App opens.
- [ ] In Settings → Audio, pick a real mic. Press `ctrl+alt+r` to start
      a manual recording. Speak for 10 s. Stop. Summary fires.

## Upgrade path

- [ ] Build a follow-up version (e.g., bump to 0.4.1 with a trivial change).
      Install it without uninstalling 0.4.0.
- [ ] Launch. First-run wizard does NOT appear (marker file still present).
- [ ] GPU runtime registers from the existing cache. App starts normally.
- [ ] Confirm `%LOCALAPPDATA%\TeamsTranscriber\runtime\nvidia\` is unchanged.

## Network failure handling

- [ ] Disconnect from the internet. Wipe the runtime cache
      (`Remove-Item %LOCALAPPDATA%\TeamsTranscriber\runtime\nvidia\* -Recurse -Force`).
- [ ] Launch the app. Wizard's GPU runtime page shows an error message.
- [ ] Reconnect. Re-launch. Wizard retries and succeeds.

## CLI mode without runtime

- [ ] Wipe the runtime cache.
- [ ] Run `TeamsTranscriber.exe serve` (or `python -m teams_transcriber serve`).
- [ ] Exits with code 2 + clear "GPU runtime not installed" message.
- [ ] Launch the GUI once. Wizard's runtime page populates the cache.
- [ ] `TeamsTranscriber.exe serve` now starts normally.

## Regression check

- [ ] Phase 5 + Phase 6 features still work: live transcription toggle,
      audio device selection, WASAPI Teams detection, workspace UI,
      hotkey rebinding, settings dialogs.
```

- [ ] **Step 8.2: Bump version**

In `pyproject.toml`, change `version = "0.3.0"` to `version = "0.4.0"`.

Also bump the `__version__` constant in `src/teams_transcriber/__init__.py`
if it's pinned to `"0.1.0"` (it is — update to `"0.4.0"`).

Run `uv sync`:

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" sync
```

- [ ] **Step 8.3: Commit version + checklist**

```powershell
git add docs/superpowers/checklists/2026-05-19-phase-7-verification.md pyproject.toml uv.lock src/teams_transcriber/__init__.py
git commit -m "chore(release): bump to 0.4.0; add Phase 7 verification checklist"
```

- [ ] **Step 8.4: Build installer**

```powershell
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" sync --all-extras
& "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run python scripts/build_installer.py
```

Expect `dist/TeamsTranscriberSetup-0.4.0.exe` at ~250 MB.

If the size is still > 600 MB, the nvidia/ exclusion in the PyInstaller
spec didn't catch everything. Inspect `dist/TeamsTranscriber/_internal/`
and add path patterns to `_is_nvidia_path` as needed.

- [ ] **Step 8.5: Push + GitHub release**

```powershell
git push -u origin feature/phase-7-decoupled-gpu-runtime
```

```powershell
& "$env:ProgramFiles\GitHub CLI\gh.exe" release create v0.4.0-rc1 `
  --target feature/phase-7-decoupled-gpu-runtime `
  --prerelease `
  --title "Phase 7 release candidate (decoupled GPU runtime)" `
  --notes "Drops installer from ~995 MB to ~250 MB. NVIDIA CUDA runtime (cuBLAS, cuDNN, cuda_nvrtc — ~700 MB) downloads from PyPI on first launch into %LOCALAPPDATA%\\TeamsTranscriber\\runtime\\nvidia\\. Updates re-download only the small installer; runtime cache persists. See docs/superpowers/checklists/2026-05-19-phase-7-verification.md for the verification flow." `
  dist/TeamsTranscriberSetup-0.4.0.exe
```

---

## Self-Review Notes

**Spec coverage check:**

- `runtime_dir` on `AppPaths` → Task 1.
- `gpu_runtime` module (constants, install detection, register) → Task 2.
- `gpu_runtime.download_runtime` with PyPI fetch + SHA256 + zip extract → Task 3.
- `__init__.py` registers cache paths → Task 4.
- First-run wizard GPU runtime page → Task 5.
- `__main__.py` bootstrap check → Task 6.
- PyInstaller spec excludes nvidia/* → Task 7.
- Verification + bump + build + release → Task 8.

**Type / signature consistency:**

- `package_dir(runtime_base, name, version)` consistent across Tasks 2, 3.
- `is_runtime_installed(runtime_base)` consistent.
- `download_runtime(runtime_base, progress_callback=None)` consistent.
- `register_runtime(runtime_base) -> bool` consistent.
- `REQUIRED_PACKAGES: list[tuple[str, str]]` consistent.

**Out of scope (deferred):**

- Cleaning up old runtime version dirs on version bumps.
- Network download resume.
- CPU-only flavor (independent question).
