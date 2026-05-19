# Phase 7 — Decoupled GPU Runtime

**Date:** 2026-05-19
**Status:** Approved (Brian, 2026-05-19)
**Branch:** `feature/phase-7-decoupled-gpu-runtime` (off Phase 6)
**Drives plan:** `docs/superpowers/plans/2026-05-19-phase-7-decoupled-gpu-runtime.md`

## Goal

Pull NVIDIA's CUDA runtime DLLs (cuDNN + cuBLAS + cuda_nvrtc — ~1.9 GB
uncompressed, ~700 MB inside the LZMA2-compressed installer) **out of the
PyInstaller bundle**. The app downloads them from PyPI on first launch into
a per-user runtime cache, the same way the Whisper model already downloads
on first inference. Updates only redeliver the small app bundle; the runtime
cache persists.

**Result:**
- Installer drops from ~995 MB → ~250 MB.
- One-time first-launch download of ~700 MB of NVIDIA wheels.
- App updates (e.g., 0.3.x → 0.4.0) re-download only the 250 MB installer.

## Non-goals (deferred)

- CPU-only build flavor (separate concern; would skip the runtime download entirely).
- Cherry-picking cuDNN by GPU architecture (separate concern; partial savings only).
- Choosing between bundled-by-default + opt-in-decoupled (one mode for now: always decoupled).

## Background — what changes vs. Phase 6

| Concern | Phase 6 | Phase 7 |
|---|---|---|
| Installer size | ~995 MB | ~250 MB |
| NVIDIA libs origin | PyInstaller bundle | Downloaded from PyPI on first launch |
| Storage location for runtime | `dist/TeamsTranscriber/_internal/nvidia/` | `%LOCALAPPDATA%\TeamsTranscriber\runtime\nvidia\<version>\` |
| DLL discovery (`__init__.py`) | Walks bundle path | Walks runtime cache path |
| First-run wizard | Welcome → API key → Whisper model | Welcome → API key → **GPU runtime** → Whisper model |
| Updates | Re-bundle all 995 MB | Re-bundle 250 MB; runtime persists |

## Architecture

### Components

**`src/teams_transcriber/runtime/gpu_runtime.py` (new module).**

Pure logic, no UI. Pulls the right NVIDIA wheels from PyPI's JSON API,
verifies their SHA256 against the metadata, extracts them (wheels are zip
files) into a per-user, per-version runtime cache, and registers the
extracted DLL directories with Windows. Imports nothing from `faster_whisper`
or `ctranslate2` — those are downstream of this module.

Public API:

```python
RUNTIME_BASE_DIR = AppPaths().runtime_dir / "nvidia"

REQUIRED_PACKAGES: list[tuple[str, str]] = [
    ("nvidia-cublas-cu12",      "12.4.5.8"),     # version pins
    ("nvidia-cudnn-cu12",       "9.1.0.70"),
    ("nvidia-cuda-nvrtc-cu12",  "12.4.127"),
]

def is_runtime_installed() -> bool:
    """True if every REQUIRED_PACKAGES version is present under RUNTIME_BASE_DIR."""

def download_runtime(progress_callback: Callable[[str, int, int], None] | None = None) -> None:
    """Download + extract all REQUIRED_PACKAGES. Raises GpuRuntimeError on failure.
    Callback signature: (package_name, bytes_done, bytes_total)."""

def register_runtime() -> bool:
    """Add the runtime's bin/ dirs to os.add_dll_directory + PATH.
    Returns True if successful, False if runtime not installed (callers fall back)."""
```

**`src/teams_transcriber/__init__.py` (modified).**

Currently runs the cuBLAS-DLL-discovery dance unconditionally at module
import. Phase 7 makes that conditional:

1. If a bundled `nvidia/` dir exists alongside the package (i.e., the
   legacy Phase 6 path), use it (back-compat for development from the venv,
   where pip-installed `nvidia-*` packages still sit there).
2. Else if `is_runtime_installed()` returns True, call `register_runtime()`.
3. Else: silently skip. ctranslate2 won't be importable until the runtime
   is installed, but it's not needed yet at this point in the import graph.

The `__init__.py`'s side-effect imports (`from teams_transcriber.runtime import gpu_runtime`)
must NOT chain into `ctranslate2` or `faster_whisper`. Verified by reading
the existing `faster_whisper` imports — they're all inside function bodies
(deferred), so we're safe.

**`src/teams_transcriber/__main__.py` (modified).**

The entry point. Before any UI or pipeline code runs:

1. Check `is_runtime_installed()`.
2. If not installed:
   - In UI mode (`python -m teams_transcriber`): run the first-run wizard,
     which gains a "Download GPU runtime" step before the existing Whisper
     model download step.
   - In headless / CLI mode (`python -m teams_transcriber serve`): print
     a clear error message + exit code 2. The user can re-launch the UI
     once to populate the runtime.
3. If installed (or just-now installed by the wizard): proceed with the
   normal app start.

**`src/teams_transcriber/ui/first_run_wizard.py` (modified).**

Insert a new `GpuRuntimePage` between the existing API key page and the
Whisper model page. The new page:

- Shows a progress bar.
- On `Next` (or auto-start on entering the page): kicks off
  `gpu_runtime.download_runtime(progress_callback=...)` on a worker thread,
  forwards progress to the bar.
- Disables Next/Cancel while downloading; re-enables when finished or
  when a failure surfaces.
- On failure: shows the exception message + a Retry button.
- On success: auto-advances to the next page.

The existing Whisper-model page is unchanged in structure — the runtime
must be installed before the Whisper download starts (because faster-whisper
needs CTranslate2, which needs the NVIDIA DLLs at import).

**`teams_transcriber.spec` (PyInstaller spec, modified).**

After `Analysis(...)`, filter out everything under `nvidia/` from the
collected binaries and datas:

```python
a.binaries = [b for b in a.binaries if not _is_nvidia_path(b[0])]
a.datas    = [d for d in a.datas    if not _is_nvidia_path(d[0])]
```

`_is_nvidia_path(path)` returns True if `path` starts with `nvidia\\` or
contains `\\nvidia\\` (handles both leading-relative and absolute paths).

**`src/teams_transcriber/paths.py` (small modification).**

Add a `runtime_dir` property that returns `%LOCALAPPDATA%\TeamsTranscriber\runtime\`,
analogous to the existing `audio_dir`, `config_dir`, `logs_dir`. Ensures it
on `ensure_dirs()`.

### Sourcing the wheels

**Use PyPI's JSON API.** For each required package, hit
`https://pypi.org/pypi/<package>/<version>/json` to get the wheel URL +
SHA256. Download the wheel (it's a zip file), verify SHA256, extract it
into `<RUNTIME_BASE_DIR>/<package>-<version>/` using `zipfile`.

The extracted layout looks like:

```
%LOCALAPPDATA%\TeamsTranscriber\runtime\nvidia\
  nvidia-cublas-cu12-12.4.5.8\
    nvidia\
      cublas\
        bin\
          cublasLt64_12.dll
          cublas64_12.dll
        ...
  nvidia-cudnn-cu12-9.1.0.70\
    nvidia\
      cudnn\
        bin\
          cudnn_adv64_9.dll
          ...
  nvidia-cuda-nvrtc-cu12-12.4.127\
    nvidia\
      cuda_nvrtc\
        bin\
          nvrtc64_120_0.dll
          ...
```

`register_runtime()` walks each version directory, finds `bin/` folders,
and adds them via both `os.add_dll_directory` and `PATH` prepend (the same
dance the existing `__init__.py` does today).

### Data flow — first launch

```
__main__.py
   │
   ├── is_runtime_installed()?
   │       │
   │       ├── True  → register_runtime() → continue with normal app start
   │       │
   │       └── False → FirstRunWizard.exec()
   │                       │
   │                       ├── Welcome page
   │                       ├── API key page (existing)
   │                       ├── GPU runtime page (NEW)
   │                       │       │
   │                       │       ├── download_runtime(progress=...)
   │                       │       │     for each package in REQUIRED_PACKAGES:
   │                       │       │        - GET https://pypi.org/pypi/<pkg>/<ver>/json
   │                       │       │        - download wheel from .urls[].url
   │                       │       │        - verify SHA256 against .urls[].digests.sha256
   │                       │       │        - zipfile.ZipFile(wheel).extractall(target_dir)
   │                       │       │        - emit progress callback
   │                       │       │
   │                       │       └── register_runtime()
   │                       │
   │                       └── Whisper model page (existing — downloads ~3 GB from HuggingFace)
   │
   └── Pipeline + UI normally
```

### Data flow — subsequent launches

```
__main__.py
   │
   ├── is_runtime_installed() == True → register_runtime() → normal start
```

### Update flow

When the user upgrades 0.3.0 → 0.4.0 (with the same pinned NVIDIA versions):

```
Installer overwrites %LOCALAPPDATA%\Programs\TeamsTranscriber\ (the app bundle).
%LOCALAPPDATA%\TeamsTranscriber\runtime\nvidia\ stays untouched.
First launch of 0.4.0: is_runtime_installed() returns True (same versions),
register_runtime() runs, app starts normally. No re-download.
```

When the user upgrades 0.4.0 → 0.5.0 with a different pinned NVIDIA version:

```
0.5.0 has REQUIRED_PACKAGES including (e.g.) nvidia-cudnn-cu12 9.2.0.
is_runtime_installed() returns False because the 9.2.0 dir doesn't exist
under runtime/nvidia/.
First-run wizard reappears just for the GPU runtime page; the API key + model
pages are skipped (the wizard sees they're already configured).
Old 9.1.0 dir stays on disk — harmless, can be cleaned up later by hand or
by an optional "clean unused runtimes" step. (Out of scope for v1 of Phase 7.)
```

### Error handling

| Failure mode | Behavior |
|---|---|
| PyPI network error mid-download | Caught; wizard shows error + Retry button. No partial files left behind (write to temp file, rename on success). |
| SHA256 mismatch | Treat as corrupt; delete partial; offer retry. Log the expected vs. actual hash. |
| Wheel extraction fails (disk full, permissions) | Clear error message in wizard. |
| Runtime download succeeded but `register_runtime()` finds 0 bin/ dirs | Treat as corrupt; force re-download on next launch. |
| Headless launch with no runtime | Exit code 2 + message: "GPU runtime not installed. Launch the GUI once to set it up." |
| User cancels mid-download | Allowed only after a current-package finishes (graceful checkpoint). The wizard remembers progress on next launch. |

### Testing

**Unit tests (no network):**
- `gpu_runtime.is_runtime_installed()` — returns False on empty dir, True when all version dirs exist.
- `gpu_runtime._verify_sha256()` — known-good and known-bad hash inputs.
- `gpu_runtime._extract_wheel(zip_path, target_dir)` — extract a tiny synthetic wheel into a tmp dir.
- `gpu_runtime.register_runtime()` — walks a fake runtime tree under tmp, verifies the right paths get added to `os.add_dll_directory` (mocked).
- `gpu_runtime.download_runtime()` — with the PyPI URL fetcher and the wheel downloader monkeypatched, verifies sequential per-package processing, progress callback invocation, error propagation.

**Integration test:**
- First-run wizard's GpuRuntimePage in offscreen Qt mode, with `download_runtime` patched to a fake that fires progress callbacks then completes — verifies the page advances correctly.

**Manual / smoke:**
- Fresh install with no cached runtime: launch, walk through the wizard, verify the runtime downloads and the app starts.
- Clean machine without an NVIDIA GPU: launch, walk through the wizard, observe that the runtime still downloads (the wheels download regardless of hardware; faster-whisper itself errors at inference time if no GPU). This is fine — separate concern from Phase 7.

## Risks

| Risk | Mitigation |
|---|---|
| The pinned NVIDIA version doesn't match what `faster_whisper`/`ctranslate2` expects → DLL load fails | Pin both `nvidia-*` AND `ctranslate2`/`faster_whisper` together in `pyproject.toml`. Test against a real Whisper inference run during build. |
| User without working internet on first launch | Wizard's GpuRuntimePage shows clear error + Retry. App is unusable until runtime exists — same as the existing Whisper model dependency. |
| PyPI wheel filenames / URL structure changes | Use the JSON API (`/pypi/<pkg>/<ver>/json`) which is stable and contracted. Avoid scraping the simple HTML index. |
| 700 MB download on first launch is slow / unreliable | Show progress; per-package retry; resume not supported in v1 (whole-package redo on failure — acceptable given the user is in the wizard, not background). |
| Old runtime dirs accumulate on disk on version upgrades | Acceptable for v1. A later "Clean unused runtimes" Settings button is trivial to add. |
| `os.add_dll_directory` doesn't transit across subprocess boundaries | Pipeline already uses threads, not subprocesses, for the faster-whisper inference path. No issue. |
| PyInstaller spec change accidentally drops something else | `_is_nvidia_path` is conservative; only matches `nvidia\\` segments. Existing test suite + smoke run catches a broken bundle before release. |
