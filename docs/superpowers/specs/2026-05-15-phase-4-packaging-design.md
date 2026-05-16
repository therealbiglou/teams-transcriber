# Phase 4 — Packaging Design

## Goal

Produce a one-click Windows installer that installs Teams Transcriber to a
clean machine without requiring Python, `uv`, or a dev environment. The
installed app starts on logon, shows the existing PySide6 UI, and can
record/transcribe/summarize Teams meetings.

## Non-goals

- **Auto-update.** Out of scope. The user re-runs the installer to upgrade.
- **Signed installer.** Stubbed in the build script but not wired by default;
  see "Code signing" below. Personal-tool ergonomics — Brian can dismiss the
  SmartScreen warning the first time.
- **Cross-platform builds.** Windows-only; matches the rest of the project.
- **Multi-user / per-machine install.** Single-user, installs to
  `%LOCALAPPDATA%`.
- **Bundling the Whisper model.** Adds ~3.1 GB; download on first run
  instead. The HuggingFace cache survives upgrades.

## Stack & Tools

| Tool | Role | Notes |
|---|---|---|
| **PyInstaller** | Freeze Python → `dist/TeamsTranscriber/` | onedir mode. Driven by `teams_transcriber.spec`. |
| **Inno Setup 6** | Wrap onedir into `.exe` installer | Free, MIT, the de-facto standard. Brian installs once; build script auto-detects `ISCC.exe`. |
| **Python build script** | Orchestrate clean → freeze → smoke-test → installer compile | `scripts/build_installer.py`. Uses `uv` from the dev venv. |

## Architecture

### Frozen layout (PyInstaller output)

```
dist/TeamsTranscriber/
├── TeamsTranscriber.exe           # GUI launcher (pythonw-equivalent, no console)
├── teams_transcriber/_internal/   # Python stdlib, site-packages, our package
│   ├── av/                        # PyAV native libs
│   ├── ctranslate2/               # whisper backend
│   ├── nvidia/                    # CUDA wheels (cublas, cudnn, nvrtc)
│   ├── PySide6/                   # Qt + plugins
│   └── ...
└── (lib*.dll, qt plugins at top level if PyInstaller decides)
```

### Installer layout (Inno Setup output)

```
%LOCALAPPDATA%\Programs\TeamsTranscriber\
  (PyInstaller onedir contents)

Start Menu\Programs\Teams Transcriber.lnk
Desktop\Teams Transcriber.lnk          (optional, on by default)
HKCU\...\Run\TeamsTranscriber          (written by the app on first launch
                                        with auto_launch=true, NOT by the
                                        installer — keeps the autolaunch
                                        logic in one place)
```

Per-user install means no UAC prompt during install. Per-user uninstall
entry registered under `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\TeamsTranscriber`.

### Runtime path resolution (frozen vs source)

`teams_transcriber/__init__.py` currently registers the CUDA DLL paths from
`.venv/Lib/site-packages/nvidia/.../bin/` via `os.add_dll_directory` + `PATH`.
This must continue to work when frozen, where those libs live next to the
exe.

Approach: detect frozen mode (`getattr(sys, "frozen", False)`) and switch the
search-roots to `Path(sys._MEIPASS) / "nvidia"` (or wherever PyInstaller drops
them).

### Autolaunch behavior change

When frozen, `autolaunch.enable()` should register the .exe path, not a
`pythonw -m teams_transcriber` command line:

```python
if getattr(sys, "frozen", False):
    return f'"{Path(sys.executable)}"'      # the .exe IS the launcher
else:
    return f'"{pythonw}" -m teams_transcriber'  # dev mode
```

Both forms parse correctly through the Windows Run loader.

## First-run wizard

A small `QDialog`-based wizard shown on first launch (detected by absence of
a marker file `%LOCALAPPDATA%\TeamsTranscriber\config\.first-run-complete`).

**Pages:**

1. **Welcome.** One paragraph: "Teams Transcriber records your meetings,
   transcribes them on your GPU, and summarizes with Claude. Press Next to
   set up."
2. **Anthropic API key.** Optional. Same `QLineEdit` style as the settings
   dialog. "Get a key at console.anthropic.com." Stored in keyring on
   accept. Skippable — user can add it later in Settings.
3. **Whisper model download.** Triggers the HF download with a progress
   bar (~3.1 GB). Required — recording without the model fails. Allow
   cancel (returns user to step 2 with a "Quit" option).
4. **Start on login.** Checkbox, default on. Writes the autolaunch entry.
5. **Done.** "You're set. Teams Transcriber lives in the system tray —
   look for the icon. Test it by starting a Meet Now in Teams."

Existing `SettingsDialog` already covers steps 2 + 4 — wizard reuses those
widgets where possible.

Implementation: `ui/first_run_wizard.py`. Shown by `App.__init__` before
`pipeline.serve()` if the marker file is missing.

## Build pipeline

`scripts/build_installer.py`:

1. **Clean.** `rm -rf dist/ build/ *.spec.bak` (preserve hand-edited .spec).
2. **Freeze.** `uv run pyinstaller teams_transcriber.spec --noconfirm`.
3. **Smoke test.** Launch `dist/TeamsTranscriber/TeamsTranscriber.exe`
   with `--smoke-test` flag (new CLI sub-command that boots, verifies the
   pipeline imports, and exits 0).
4. **Inno Setup.** Compile `installer/teams-transcriber.iss`. Output:
   `dist/TeamsTranscriberSetup-<version>.exe`.
5. **(Optional) Sign.** If `TT_SIGN_CERT_PATH` env var is set, invoke
   `signtool sign /f <cert> /p <password> /tr ... /td sha256 <installer>`.
6. **Report.** Print final installer path + size.

Version pulled from `pyproject.toml` via `importlib.metadata`.

`teams_transcriber.spec` (PyInstaller config) lives at the repo root:
- `Analysis` with explicit `datas` for Qt plugins, PySide6 fonts (the
  warning seen in tests), and any hidden imports for ctranslate2.
- `hiddenimports` for `keyring.backends.Windows`, `winreg`, anything
  PyInstaller's import-graph might miss because of conditional `import` in
  our modules.

## Code signing (deferred)

Without a cert, Windows SmartScreen warns users on first run of any
unknown publisher. For Brian's personal use this is a one-time dismiss —
acceptable. If he wants to remove the warning (e.g., to install on a
second machine without re-deciding), he needs a code-signing certificate:

- **Standard OV cert:** ~$100/year (Sectigo, DigiCert, GoDaddy resellers).
- **Azure Trusted Signing:** ~$10/month, no hardware token required.

Build script reads `TT_SIGN_CERT_PATH` + `TT_SIGN_CERT_PASSWORD` from env
and invokes `signtool` if present. No-cert path is the default.

**Open item for Brian:** decide on signing approach later. Until then the
unsigned installer works fine for self-install.

## Testing

| Layer | Coverage |
|---|---|
| `tests/test_packaging.py` | Unit-test the version-from-pyproject helper and any path resolution helpers (frozen-mode flag handling). |
| `tests/test_autolaunch.py` | Add a test that frozen-mode command-building uses `sys.executable` directly. |
| `tests/ui/test_first_run_wizard.py` | Verify the wizard saves API key, sets autolaunch, writes the marker file. Mock the model download. |
| Manual smoke | Run `python scripts/build_installer.py`, install the output, verify: launches, transcribes a Meet Now, autostarts on reboot. |

PyInstaller-specific behavior (DLL loading, frozen-mode paths) is hard to
unit-test without actually building the bundle. The `--smoke-test` CLI
subcommand and the manual install run carry that load.

## Components touched

| File | Change |
|---|---|
| `src/teams_transcriber/autolaunch.py` | Branch on `sys.frozen` for command-line builder |
| `src/teams_transcriber/__init__.py` | Branch on `sys.frozen` for CUDA DLL search-roots |
| `src/teams_transcriber/cli.py` | Add `smoke-test` subcommand |
| `src/teams_transcriber/ui/app.py` | Detect first run, show wizard before serving |
| `src/teams_transcriber/ui/first_run_wizard.py` | New module |
| `src/teams_transcriber/paths.py` | Add `first_run_marker_path` |
| `teams_transcriber.spec` | New |
| `installer/teams-transcriber.iss` | New (Inno Setup script) |
| `scripts/build_installer.py` | New |
| `pyproject.toml` | Add `pyinstaller` as dev dep |
| `tests/test_packaging.py` | New |
| `tests/test_autolaunch.py` | One test for frozen path |
| `tests/ui/test_first_run_wizard.py` | New |
| `README.md` / docs | Document the build + Inno-Setup-install one-liner |

## Open items deferred to Brian

1. **Code signing cert / Azure Trusted Signing subscription.** Build script
   accepts cert via env var; no purchase required to ship the unsigned MVP.
2. **Inno Setup install.** Brian needs to install Inno Setup 6 once on his
   dev machine; the build script auto-detects `ISCC.exe`.
3. **Desktop shortcut default.** Spec defaults to creating one; trivial
   to flip.
4. **App icon.** Need a final `.ico`. Build will use a placeholder if none
   exists; spec includes the path stub.

## Success criteria

- `python scripts/build_installer.py` produces `dist/TeamsTranscriberSetup-<version>.exe`
  on Brian's dev machine with zero manual intervention.
- Running the installer on a clean Windows account installs the app to
  `%LOCALAPPDATA%\Programs\TeamsTranscriber` without UAC, creates a Start
  Menu entry, and (after first launch) registers autostart.
- First launch shows the wizard; after completion the marker file exists
  and subsequent launches skip the wizard.
- Recording a real Teams meeting end-to-end works on a clean install
  (modulo the Whisper-model download on the wizard's step 3).
- 164 existing tests still pass; new tests cover the frozen-mode branches
  and first-run wizard flow.
