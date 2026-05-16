# Phase 4 Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a one-click Windows installer that puts Teams Transcriber on a clean machine with no Python or dev tooling required.

**Architecture:** PyInstaller onedir bundles the venv into a frozen directory; Inno Setup wraps that into a user-mode installer to `%LOCALAPPDATA%\Programs\TeamsTranscriber`. App detects `sys.frozen` to switch its CUDA-DLL search roots and autolaunch command-line. A first-run wizard handles API key + Whisper model download.

**Tech Stack:** PyInstaller 6+, Inno Setup 6, existing PySide6 + faster-whisper + anthropic stack.

**Spec:** `docs/superpowers/specs/2026-05-15-phase-4-packaging-design.md`

**Branch:** `feature/phase-4-packaging` (rebase from `main`).

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `pyproject.toml` | modify | Add `pyinstaller` dev dep |
| `src/teams_transcriber/autolaunch.py` | modify | Branch on `sys.frozen` |
| `src/teams_transcriber/__init__.py` | modify | Branch CUDA path roots on `sys.frozen` |
| `src/teams_transcriber/paths.py` | modify | Add `first_run_marker_path` |
| `src/teams_transcriber/cli.py` | modify | Add `smoke-test` subcommand |
| `src/teams_transcriber/ui/first_run_wizard.py` | create | New wizard QDialog |
| `src/teams_transcriber/ui/app.py` | modify | Show wizard on first launch |
| `teams_transcriber.spec` | create | PyInstaller config |
| `installer/teams-transcriber.iss` | create | Inno Setup script |
| `installer/icon.ico` | create | Placeholder app icon |
| `scripts/build_installer.py` | create | Build orchestrator |
| `README.md` | modify | Document build process |
| `docs/superpowers/checklists/2026-05-15-phase-4-verification.md` | create | Manual verification checklist |
| `tests/test_autolaunch.py` | modify | Frozen-mode tests |
| `tests/test_cli.py` | modify | Smoke-test subcommand test |
| `tests/test_paths.py` | modify | first_run_marker_path test |
| `tests/ui/test_first_run_wizard.py` | create | Wizard widget tests |

---

## Task 1: Add PyInstaller to dev dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Read current dev-dependencies**

Run: `grep -A 20 "dev-dependencies\|optional-dependencies\|dev = " pyproject.toml`

Locate the dev/test dependency group.

- [ ] **Step 2: Add pyinstaller**

In the dev dep array, add `"pyinstaller>=6.0",` alphabetically. Example resulting block:

```toml
[tool.uv]
dev-dependencies = [
    "pyinstaller>=6.0",
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "pytest-qt>=4.4",
]
```

- [ ] **Step 3: Install via uv**

Run: `uv sync --dev`
Expected: pyinstaller and its deps install without error.

- [ ] **Step 4: Smoke-check pyinstaller is importable**

Run: `.venv/Scripts/python.exe -c "import PyInstaller; print(PyInstaller.__version__)"`
Expected: Prints a version >= 6.0.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add pyinstaller for Phase 4 packaging"
```

---

## Task 2: Detect frozen-mode in autolaunch command builder

**Files:**
- Modify: `src/teams_transcriber/autolaunch.py:30-35` (the `_build_launch_command` function)
- Modify: `tests/test_autolaunch.py` (add new test)

When PyInstaller freezes the app, `sys.executable` IS the launcher (`TeamsTranscriber.exe`), not Python. The Run-key value should be the bare exe path with no `-m teams_transcriber` argument.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_autolaunch.py`:

```python
def test_build_launch_command_frozen_uses_sys_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """When frozen, the Run-key value is just the .exe path — no -m flag."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\TeamsTranscriber\TeamsTranscriber.exe")
    cmd = autolaunch._build_launch_command()
    assert cmd == r'"C:\Apps\TeamsTranscriber\TeamsTranscriber.exe"'


def test_build_launch_command_source_uses_pythonw_and_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Source mode keeps the pythonw -m teams_transcriber form."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd = autolaunch._build_launch_command()
    assert "-m teams_transcriber" in cmd
    assert "pythonw" in cmd.lower() or "python" in cmd.lower()
```

- [ ] **Step 2: Run tests to verify the frozen one fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_autolaunch.py::test_build_launch_command_frozen_uses_sys_executable -v`
Expected: FAIL — current code always returns `pythonw -m teams_transcriber`.

- [ ] **Step 3: Update `_build_launch_command`**

Replace the function body in `src/teams_transcriber/autolaunch.py`:

```python
def _build_launch_command() -> str:
    """Full Windows command-line that launches the UI without a console window."""
    if getattr(sys, "frozen", False):
        # PyInstaller-frozen — sys.executable IS the launcher .exe.
        return f'"{sys.executable}"'
    py = Path(sys.executable)
    pythonw = py.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else py
    return f'"{interpreter}" -m teams_transcriber'
```

- [ ] **Step 4: Run both new tests + the existing autolaunch suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_autolaunch.py -v`
Expected: 5/5 passing.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/autolaunch.py tests/test_autolaunch.py
git commit -m "feat(autolaunch): register .exe path directly when frozen by PyInstaller"
```

---

## Task 3: Branch CUDA DLL search roots on frozen mode

**Files:**
- Modify: `src/teams_transcriber/__init__.py`

This is hard to unit-test because it depends on Windows DLL search behavior and the actual frozen layout. We verify by manual smoke-test in Task 10.

- [ ] **Step 1: Read the current __init__.py path-registration block**

Run: `cat src/teams_transcriber/__init__.py`

Identify the venv-relative `nvidia/<lib>/bin` glob.

- [ ] **Step 2: Wrap the path discovery in a frozen-aware helper**

Replace the path-discovery section with:

```python
def _cuda_dll_dirs() -> list[Path]:
    """Return directories holding cuBLAS/cuDNN/nvrtc DLLs, frozen-mode aware."""
    if getattr(sys, "frozen", False):
        # PyInstaller layout: bundled nvidia wheels under the onedir root.
        # _MEIPASS points at the extraction root for onefile; for onedir it
        # equals the install dir.
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        bases = [root / "nvidia"]
    else:
        venv_root = Path(__file__).resolve().parents[2] / ".venv" / "Lib" / "site-packages"
        bases = [venv_root / "nvidia"]
    dirs: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        for lib_dir in base.iterdir():
            bin_dir = lib_dir / "bin"
            if bin_dir.is_dir():
                dirs.append(bin_dir)
    return dirs
```

Then replace the `os.add_dll_directory` + `PATH` registration loop with one that consumes `_cuda_dll_dirs()`.

- [ ] **Step 3: Run the full suite to confirm dev-mode behavior is unbroken**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: All tests pass (importing the package shouldn't crash on the new helper).

- [ ] **Step 4: Smoke-check via REPL**

Run: `.venv/Scripts/python.exe -c "from teams_transcriber import _cuda_dll_dirs; print([str(p) for p in _cuda_dll_dirs()])"`
Expected: Prints one or more paths under `.venv\Lib\site-packages\nvidia\.../bin`.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/__init__.py
git commit -m "feat(boot): branch CUDA DLL search roots on sys.frozen"
```

---

## Task 4: Add first-run marker path

**Files:**
- Modify: `src/teams_transcriber/paths.py`
- Modify: `tests/storage/test_paths.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/storage/test_paths.py`:

```python
def test_first_run_marker_path_lives_under_config(tmp_path: Path) -> None:
    paths = AppPaths(root=tmp_path / "TT")
    assert paths.first_run_marker_path == tmp_path / "TT" / "config" / ".first-run-complete"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_paths.py::test_first_run_marker_path_lives_under_config -v`
Expected: FAIL — attribute does not exist.

- [ ] **Step 3: Add the property**

In `src/teams_transcriber/paths.py`, after `config_dir`:

```python
    @property
    def first_run_marker_path(self) -> Path:
        return self.config_dir / ".first-run-complete"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_paths.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/paths.py tests/storage/test_paths.py
git commit -m "feat(paths): add first_run_marker_path"
```

---

## Task 5: Add `smoke-test` CLI subcommand

**Files:**
- Modify: `src/teams_transcriber/cli.py`
- Modify: `tests/test_cli.py`

The smoke test boots the pipeline imports without launching the UI and exits 0. The build script runs this against the frozen .exe to fail fast if PyInstaller missed a dependency.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_cli_smoke_test_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    rc = main(["smoke-test"])
    assert rc == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py::test_cli_smoke_test_runs -v`
Expected: FAIL — unknown subcommand.

- [ ] **Step 3: Add the subcommand**

In `src/teams_transcriber/cli.py`, add the handler:

```python
def _cmd_smoke_test(_args: argparse.Namespace) -> int:
    """Import the full pipeline stack and exit 0. Used by the build script
    to verify the frozen .exe loads all native dependencies."""
    from teams_transcriber.audio.opus_writer import OpusWriter  # noqa: F401
    from teams_transcriber.audio.splitter import split_channels_to_wav  # noqa: F401
    from teams_transcriber.pipeline import Pipeline  # noqa: F401
    from teams_transcriber.summarizer import Summarizer  # noqa: F401
    from teams_transcriber.transcriber import Transcriber  # noqa: F401
    from teams_transcriber.ui.app import App  # noqa: F401
    print("smoke-test ok", file=sys.stderr)
    return 0
```

Register in `main()` alongside the other subparsers:

```python
    p_smoke = sub.add_parser("smoke-test", help="Boot all imports and exit 0 (build verification).")
    p_smoke.set_defaults(func=_cmd_smoke_test)
```

- [ ] **Step 4: Run tests to verify**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/cli.py tests/test_cli.py
git commit -m "feat(cli): add smoke-test subcommand for build verification"
```

---

## Task 6: Build the first-run wizard

**Files:**
- Create: `src/teams_transcriber/ui/first_run_wizard.py`
- Create: `tests/ui/test_first_run_wizard.py`

Reuses theme tokens + the API-key input pattern from `SettingsDialog`. Pages are stacked into a `QStackedWidget`; Next/Back buttons control the index. The model-download page runs the download in a `QThread` so the UI stays responsive.

- [ ] **Step 1: Write the failing test for the marker-file flow**

Create `tests/ui/test_first_run_wizard.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.config import load_settings, save_settings
from teams_transcriber.paths import AppPaths
from teams_transcriber.ui.first_run_wizard import FirstRunWizard


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT")
    p.ensure_dirs()
    return p


def test_finish_writes_marker_file(qapp, qtbot, paths: AppPaths) -> None:
    settings = load_settings(paths)
    wizard = FirstRunWizard(settings=settings, paths=paths, model_downloader=lambda cb: None)
    # Skip past all pages and finish.
    wizard.auto_launch_cb.setChecked(False)  # don't touch real registry in tests
    wizard._finish()
    assert paths.first_run_marker_path.exists()


def test_finish_persists_api_key_when_provided(qapp, qtbot, paths: AppPaths, monkeypatch) -> None:
    """API key entry path: when the user types a key, it goes to keyring."""
    settings = load_settings(paths)
    saved: dict[str, str] = {}
    monkeypatch.setattr(
        "keyring.set_password",
        lambda svc, usr, pw: saved.setdefault(usr, pw),
    )
    wizard = FirstRunWizard(settings=settings, paths=paths, model_downloader=lambda cb: None)
    wizard.api_key_input.setText("sk-test-fake")
    wizard.auto_launch_cb.setChecked(False)
    wizard._finish()
    assert saved.get("anthropic_api_key") == "sk-test-fake"


def test_finish_skips_keyring_when_api_key_blank(qapp, qtbot, paths: AppPaths, monkeypatch) -> None:
    settings = load_settings(paths)
    calls: list[tuple] = []
    monkeypatch.setattr("keyring.set_password", lambda *a: calls.append(a))
    wizard = FirstRunWizard(settings=settings, paths=paths, model_downloader=lambda cb: None)
    wizard.api_key_input.setText("")
    wizard.auto_launch_cb.setChecked(False)
    wizard._finish()
    assert calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_first_run_wizard.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the wizard**

Create `src/teams_transcriber/ui/first_run_wizard.py`:

```python
"""First-run wizard. Shown on initial app launch only.

Three pages: welcome / API key + auto-launch / model download. Saves the
API key (if provided) to keyring, writes settings, registers autolaunch
when enabled, then drops the first-run marker file so the wizard never
shows again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import keyring
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_ANTHROPIC, Settings, save_settings
from teams_transcriber.paths import AppPaths

logger = logging.getLogger(__name__)

ModelDownloader = Callable[[Callable[[int], None]], None]
"""Callable(progress_callback)->None. progress_callback receives 0..100."""


def _default_model_downloader(progress: Callable[[int], None]) -> None:
    """Trigger faster-whisper to pull the configured model into HF cache."""
    from faster_whisper import WhisperModel  # noqa: F401  (import side-effect on first use)
    progress(10)
    # The model loads from cache or downloads; faster-whisper doesn't expose
    # progress, so we coarse-grain it.
    WhisperModel("mobiuslabsgmbh/faster-whisper-large-v3-turbo", device="cpu", compute_type="int8")
    progress(100)


class FirstRunWizard(QDialog):
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
        self.setModal(True)
        self.setMinimumSize(500, 400)
        self._settings = settings
        self._paths = paths
        self._model_downloader = model_downloader

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_welcome())
        self._stack.addWidget(self._build_setup())
        self._stack.addWidget(self._build_model_download())

        layout = QVBoxLayout(self)
        layout.addWidget(self._stack, 1)
        nav = QHBoxLayout()
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._back)
        self._next_btn = QPushButton("Next")
        self._next_btn.clicked.connect(self._next)
        nav.addStretch()
        nav.addWidget(self._back_btn)
        nav.addWidget(self._next_btn)
        layout.addLayout(nav)
        self._update_nav()

    def _build_welcome(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        title = QLabel("<h2>Welcome to Teams Transcriber</h2>")
        body = QLabel(
            "Teams Transcriber records your Microsoft Teams meetings, transcribes them locally "
            "on your GPU, and summarizes them with Claude. Press Next to set up."
        )
        body.setWordWrap(True)
        v.addWidget(title)
        v.addWidget(body)
        v.addStretch()
        return w

    def _build_setup(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>Set up your account</h3>"))
        v.addWidget(QLabel(
            "Enter your Anthropic API key (you can also do this later in Settings):"
        ))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("sk-ant-...")
        v.addWidget(self.api_key_input)
        v.addWidget(QLabel("<small>Get a key at https://console.anthropic.com/</small>"))
        v.addSpacing(20)
        self.auto_launch_cb = QCheckBox("Start Teams Transcriber when I log on")
        self.auto_launch_cb.setChecked(True)
        v.addWidget(self.auto_launch_cb)
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
        v.addWidget(self.progress_label)
        v.addStretch()
        return w

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
        if self._stack.currentIndex() == 2:
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
            self.progress_label.setText(f"Model download failed: {exc}. You can retry later.")

    def _on_progress(self, pct: int) -> None:
        self.progress_bar.setValue(pct)

    def _finish(self) -> None:
        # Persist API key if provided.
        key = self.api_key_input.text().strip()
        if key:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER_ANTHROPIC, key)
        # Persist auto-launch preference + sync registry.
        self._settings._raw["general"]["auto_launch"] = self.auto_launch_cb.isChecked()
        save_settings(self._paths, self._settings)
        from teams_transcriber import autolaunch
        if self.auto_launch_cb.isChecked():
            autolaunch.enable()
        else:
            autolaunch.disable()
        # Drop the marker file so we don't show again.
        self._paths.config_dir.mkdir(parents=True, exist_ok=True)
        self._paths.first_run_marker_path.write_text("ok\n", encoding="utf-8")
        self.finished_ok.emit()
        self.accept()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_first_run_wizard.py -v`
Expected: All 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/first_run_wizard.py tests/ui/test_first_run_wizard.py
git commit -m "feat(ui): add first-run wizard (welcome / API key / model download)"
```

---

## Task 7: Wire the wizard into App startup

**Files:**
- Modify: `src/teams_transcriber/ui/app.py`

The wizard should be shown synchronously before `pipeline.serve()` if the marker is absent. If the user closes it without finishing, we still let them into the app — the marker just doesn't get written and they'll see the wizard again next time.

- [ ] **Step 1: Locate the App.__init__ block that runs autolaunch.enable()**

Run: `grep -n "auto_launch\|autolaunch" src/teams_transcriber/ui/app.py`

Find the existing autolaunch block (around line 128).

- [ ] **Step 2: Inject the wizard call before the autolaunch block**

Add to `App.__init__`, *immediately after the main window is constructed* but BEFORE the autolaunch + serve block:

```python
        if not self.paths.first_run_marker_path.exists():
            from teams_transcriber.ui.first_run_wizard import FirstRunWizard
            wizard = FirstRunWizard(
                settings=self.settings, paths=self.paths, parent=self.window,
            )
            wizard.exec()
            # Reload settings — the wizard wrote to disk.
            from teams_transcriber.config import load_settings
            self.settings = load_settings(self.paths)
```

- [ ] **Step 3: Make sure the wizard doesn't break headless test runs**

The existing UI tests construct `App` against a tmp_path. Because they call `ensure_dirs()` and then create the App, the marker file won't exist — the wizard would fire and block. Two fixes:
  - For automated testing, the wizard call is gated; only show when the QApplication is running interactively.
  - Cleaner: leave the gate as the marker file; tests that construct `App` create the marker file first as part of their setup.

Update existing app-construction tests in `tests/ui/test_main_window.py`, `tests/ui/test_tray.py` (and any other test that builds `App` directly) to call `paths.first_run_marker_path.write_text("ok\n")` BEFORE constructing the App.

- [ ] **Step 4: Run the UI test suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/ -v`
Expected: All pass — no test hangs waiting on the wizard.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py tests/ui/
git commit -m "feat(ui): show first-run wizard when marker file is absent"
```

---

## Task 8: Create the PyInstaller spec file

**Files:**
- Create: `teams_transcriber.spec` (repo root)
- Create: `installer/icon.ico` (placeholder — copy any existing 32x32+ .ico, or empty bytes with a TODO marker)

PyInstaller spec files are Python and are imported by `pyinstaller`. This task is iterative — first build will likely surface a missing hidden import or a missing data file. Plan for 2-3 rebuilds.

- [ ] **Step 1: Create the spec**

Create `teams_transcriber.spec` at repo root:

```python
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Teams Transcriber.

Run via the build script:  python scripts/build_installer.py
or directly:               pyinstaller teams_transcriber.spec --noconfirm
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

REPO_ROOT = Path(SPECPATH)
SITE_PACKAGES = REPO_ROOT / ".venv" / "Lib" / "site-packages"

# --- Collect packages with binary or data dependencies ----------------------

av_datas, av_binaries, av_hidden = collect_all("av")
ct2_datas, ct2_binaries, ct2_hidden = collect_all("ctranslate2")
sd_datas, sd_binaries, sd_hidden = collect_all("soundcard")
fw_datas, fw_binaries, fw_hidden = collect_all("faster_whisper")

# CUDA wheels — include the entire nvidia/ tree so the runtime path
# registration in __init__.py finds the DLLs.
NVIDIA_ROOT = SITE_PACKAGES / "nvidia"
cuda_binaries = []
if NVIDIA_ROOT.is_dir():
    for dll in NVIDIA_ROOT.rglob("*.dll"):
        rel = dll.relative_to(NVIDIA_ROOT)
        cuda_binaries.append((str(dll), str(Path("nvidia") / rel.parent)))

# --- Hidden imports we know are missed by static analysis -------------------

extra_hidden = [
    "keyring.backends.Windows",
    "win32timezone",
    *collect_submodules("anthropic"),
]

# --- Assembly ---------------------------------------------------------------

a = Analysis(
    [str(REPO_ROOT / "src" / "teams_transcriber" / "__main__.py")],
    pathex=[str(REPO_ROOT / "src")],
    binaries=av_binaries + ct2_binaries + sd_binaries + fw_binaries + cuda_binaries,
    datas=av_datas + ct2_datas + sd_datas + fw_datas,
    hiddenimports=[*av_hidden, *ct2_hidden, *sd_hidden, *fw_hidden, *extra_hidden],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TeamsTranscriber",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                          # windowed (no console flash)
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(REPO_ROOT / "installer" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TeamsTranscriber",
)
```

- [ ] **Step 2: Create a placeholder icon**

If a `.ico` is genuinely available, copy it into `installer/icon.ico`. Otherwise create a 16x16 transparent ICO with Python so PyInstaller doesn't fail:

```python
# scripts/make_placeholder_icon.py — one-off, then deleted
from PIL import Image
img = Image.new("RGBA", (32, 32), (16, 185, 129, 255))  # emerald accent
img.save("installer/icon.ico", sizes=[(32, 32), (16, 16)])
```

Run once: `mkdir installer; .venv/Scripts/python.exe scripts/make_placeholder_icon.py; rm scripts/make_placeholder_icon.py`

Pillow is already pulled in by PySide6 transitively — verify with `python -c "import PIL"`.

- [ ] **Step 3: First build attempt**

Run: `.venv/Scripts/python.exe -m PyInstaller teams_transcriber.spec --noconfirm --log-level WARN`
Expected: Build runs to completion (2-5 minutes); produces `dist/TeamsTranscriber/TeamsTranscriber.exe`. Warnings about excluded modules are normally OK; ERRORS are not.

If the build fails on a missing module: add it to `extra_hidden` and rebuild.

- [ ] **Step 4: Smoke-test the frozen exe**

Run: `dist/TeamsTranscriber/TeamsTranscriber.exe smoke-test`
Expected: Exits 0 with `smoke-test ok` on stderr.

Iterate on the spec until this passes.

- [ ] **Step 5: Commit**

```bash
git add teams_transcriber.spec installer/icon.ico
git commit -m "build(packaging): add PyInstaller spec for onedir bundle"
```

---

## Task 9: Create the Inno Setup installer script

**Files:**
- Create: `installer/teams-transcriber.iss`

- [ ] **Step 1: Verify Inno Setup is available**

Run: `& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /?`

If not installed, install Inno Setup 6 from https://jrsoftware.org/isdl.php (free, MIT).

- [ ] **Step 2: Create the ISS file**

Create `installer/teams-transcriber.iss`:

```iss
; Teams Transcriber installer
; Compile via: ISCC.exe /DAppVersion=x.y.z installer\teams-transcriber.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{C9F4C7E0-7E0A-4E80-9B5D-3E2A0F4F1B33}
AppName=Teams Transcriber
AppVersion={#AppVersion}
AppPublisher=Brian Lewis
AppPublisherURL=https://github.com/
DefaultDirName={localappdata}\Programs\TeamsTranscriber
DefaultGroupName=Teams Transcriber
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist
OutputBaseFilename=TeamsTranscriberSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\TeamsTranscriber.exe
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional shortcuts:"; Flags: checked

[Files]
Source: "..\dist\TeamsTranscriber\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Teams Transcriber"; Filename: "{app}\TeamsTranscriber.exe"
Name: "{group}\Uninstall Teams Transcriber"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Teams Transcriber"; Filename: "{app}\TeamsTranscriber.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TeamsTranscriber.exe"; Description: "Launch Teams Transcriber"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
```

- [ ] **Step 3: Verify it compiles standalone (no PyInstaller output needed)**

ISCC won't fail if dist/ is absent, but [Files] glob will produce an empty installer. Run a dry compile against an existing dist/ if Task 8 has been run:

Run: `& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 installer\teams-transcriber.iss`

Expected: Produces `dist/TeamsTranscriberSetup-0.1.0.exe`. Size should be several hundred MB (Whisper-less but CUDA-laden).

- [ ] **Step 4: Commit**

```bash
git add installer/teams-transcriber.iss
git commit -m "build(packaging): add Inno Setup script for user-mode installer"
```

---

## Task 10: Create the build orchestrator

**Files:**
- Create: `scripts/build_installer.py`

- [ ] **Step 1: Implement the orchestrator**

Create `scripts/build_installer.py`:

```python
#!/usr/bin/env python
"""Build Teams Transcriber installer end-to-end.

Steps:
  1. Read app version from pyproject.toml.
  2. Clean dist/ and build/.
  3. Run PyInstaller against teams_transcriber.spec.
  4. Smoke-test the frozen .exe.
  5. Find ISCC.exe and compile the Inno Setup script.
  6. (Optional) Sign the installer if TT_SIGN_CERT_PATH is set.
  7. Report final installer path + size.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def app_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        pp = tomllib.load(f)
    return str(pp["project"]["version"])


def find_iscc() -> Path:
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        "ISCC.exe not found. Install Inno Setup 6 from https://jrsoftware.org/isdl.php"
    )


def step(name: str) -> None:
    print(f"\n=== {name} ===", flush=True)


def run(cmd: list[str], **kw) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.check_call(cmd, **kw)


def main() -> int:
    version = app_version()
    print(f"Building Teams Transcriber {version}")

    step("Clean")
    for d in ("dist", "build"):
        p = REPO_ROOT / d
        if p.exists():
            shutil.rmtree(p)
            print(f"removed {p}")

    step("PyInstaller")
    run(
        [sys.executable, "-m", "PyInstaller", "teams_transcriber.spec",
         "--noconfirm", "--log-level", "WARN"],
        cwd=REPO_ROOT,
    )

    step("Smoke-test")
    exe = REPO_ROOT / "dist" / "TeamsTranscriber" / "TeamsTranscriber.exe"
    if not exe.is_file():
        raise SystemExit(f"PyInstaller did not produce {exe}")
    run([str(exe), "smoke-test"])

    step("Inno Setup")
    iscc = find_iscc()
    run(
        [str(iscc), f"/DAppVersion={version}",
         str(REPO_ROOT / "installer" / "teams-transcriber.iss")],
        cwd=REPO_ROOT / "installer",
    )

    step("Sign (optional)")
    cert = os.environ.get("TT_SIGN_CERT_PATH")
    pw = os.environ.get("TT_SIGN_CERT_PASSWORD", "")
    installer = REPO_ROOT / "dist" / f"TeamsTranscriberSetup-{version}.exe"
    if cert:
        run([
            "signtool", "sign",
            "/f", cert, "/p", pw,
            "/tr", "http://timestamp.digicert.com",
            "/td", "sha256", "/fd", "sha256",
            str(installer),
        ])
    else:
        print("(skipped — set TT_SIGN_CERT_PATH to sign)")

    step("Done")
    size_mb = installer.stat().st_size / (1024 * 1024)
    print(f"Installer: {installer}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify the script's dry parts run**

Run: `.venv/Scripts/python.exe -c "from scripts.build_installer import app_version, find_iscc; print(app_version()); print(find_iscc())"`
Expected: Prints the version (e.g. `0.1.0`) and the ISCC.exe path. If `find_iscc` raises, install Inno Setup.

- [ ] **Step 3: End-to-end build**

Run: `.venv/Scripts/python.exe scripts/build_installer.py`
Expected: Final line `Installer: <path>  (~XYZ MB)`. Takes 3-8 minutes.

- [ ] **Step 4: Commit**

```bash
git add scripts/build_installer.py
git commit -m "build(packaging): add build_installer.py orchestrator"
```

---

## Task 11: Document the build in README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Building the installer" section**

Append to README.md (or insert in a sensible location):

```markdown
## Building the installer

Prerequisites (one-time):
- [Inno Setup 6](https://jrsoftware.org/isdl.php)
- Project venv populated: `uv sync --dev`

Then:

```powershell
uv run python scripts/build_installer.py
```

Output: `dist/TeamsTranscriberSetup-<version>.exe` (user-mode installer to
`%LOCALAPPDATA%\Programs\TeamsTranscriber`, no UAC).

To sign the installer, set `TT_SIGN_CERT_PATH` and `TT_SIGN_CERT_PASSWORD`
in the environment before running the build script.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): document build_installer.py usage"
```

---

## Task 12: Phase 4 manual verification checklist

**Files:**
- Create: `docs/superpowers/checklists/2026-05-15-phase-4-verification.md`

- [ ] **Step 1: Write the checklist**

Create the file:

```markdown
# Phase 4 — Manual Verification Checklist

Run these after a fresh `python scripts/build_installer.py`.

## Installer flow

- [ ] Run `dist\TeamsTranscriberSetup-<version>.exe` on a Windows account
      that has never installed Teams Transcriber.
- [ ] No UAC prompt during install.
- [ ] Install completes; Start Menu entry "Teams Transcriber" exists.
- [ ] Desktop shortcut exists (if Tasks/desktopicon was checked).
- [ ] App launches from Start Menu.

## First-run wizard

- [ ] Welcome page shows on first launch.
- [ ] API key page accepts a paste of a real `sk-ant-...` key.
- [ ] Skipping the API key still allows finishing the wizard.
- [ ] "Start on login" checkbox state persists.
- [ ] Model download progresses (or completes if cache already exists).
- [ ] Finish closes the wizard; main app opens to history.
- [ ] `%LOCALAPPDATA%\TeamsTranscriber\config\.first-run-complete` exists.
- [ ] Next launch SKIPS the wizard.

## Functional pipeline

- [ ] Start a Meet Now in Teams; meeting is auto-detected.
- [ ] Recording row appears in history with status "Recording".
- [ ] Ending the meeting triggers transcribe → summarize.
- [ ] Summary populates with todos / actions / topics.
- [ ] Manual recording (hotkey) works.

## Autolaunch

- [ ] Reboot the machine.
- [ ] Teams Transcriber appears in the system tray automatically.
- [ ] No console window flash.
- [ ] `reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v TeamsTranscriber`
      shows the .exe path with NO `-m teams_transcriber` argument.

## Uninstall

- [ ] Settings → Apps → Teams Transcriber → Uninstall removes the install dir.
- [ ] Autolaunch entry is removed (app's UninstallDelete step, or manual cleanup).
- [ ] User data under `%LOCALAPPDATA%\TeamsTranscriber\` is PRESERVED
      (recordings, db). Reinstalling restores them.

## Signed build (only if cert configured)

- [ ] Installer triggers no SmartScreen warning on a clean machine.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/checklists/2026-05-15-phase-4-verification.md
git commit -m "docs(checklist): add Phase 4 manual verification checklist"
```

---

## Wrap-up after all tasks

After all 12 tasks land on `feature/phase-4-packaging`:

- [ ] Run the full test suite. Expected: 167+ passing (164 + new tests).
- [ ] Run `python scripts/build_installer.py` end-to-end. Expected: produces a usable installer.
- [ ] Walk through the manual verification checklist on Brian's machine.
- [ ] Invoke `superpowers:finishing-a-development-branch` to merge to `main` with `--no-ff`.

## Open items requiring Brian's input

- **Code signing certificate.** Build script accepts `TT_SIGN_CERT_PATH`;
  Brian decides if/when to procure a cert. Without it, SmartScreen warns
  on first run (one-time dismiss).
- **App icon.** Task 8 ships a generated emerald-square placeholder.
  Replace `installer/icon.ico` with a real icon when Brian provides one.
- **Inno Setup install on Brian's dev machine.** One-time; build script
  surfaces a clear error if missing.
