# Teams Transcriber — Claude context

A personal-use Windows app that watches for Microsoft Teams meetings,
records dual-channel audio (mic + system loopback), transcribes locally
on the GPU via faster-whisper, and uses Claude to produce structured
summaries with action items, to-dos, decisions, and follow-ups.

Phases 1-4 (foundation, pipeline, real audio + hardening, UI,
polish, packaging) have all shipped on `main`. The full spec lives at
[`docs/superpowers/specs/2026-05-14-teams-transcriber-design.md`](docs/superpowers/specs/2026-05-14-teams-transcriber-design.md);
per-phase plans are in `docs/superpowers/plans/`; manual-verification
checklists are in `docs/superpowers/checklists/`.

## Stack

- Python 3.11, managed with **uv** (NOT pip/venv/poetry — use `uv sync`
  / `uv run` / `uv add`).
- **PySide6** (Qt 6) for the desktop UI.
- **faster-whisper** (CTranslate2) on CUDA via NVIDIA cuBLAS/cuDNN pip
  wheels. The cuBLAS DLL discovery dance happens in
  `src/teams_transcriber/__init__.py` (both `os.add_dll_directory` and
  `PATH` prepend are required on Windows).
- **soundcard** for WASAPI mic + loopback capture (dual-stream).
- **av** (PyAV) for Opus encode + per-channel decode → WAV for Whisper.
- **anthropic** SDK with tool-use structured output + prompt caching.
- **SQLite** (stdlib) with FTS5; migrations via `PRAGMA user_version`.
- **keyring** (Windows Credential Manager) for the Anthropic API key.
- **pywin32 + psutil** for window enumeration; **keyboard** for hotkeys.

## Common commands

```powershell
uv sync --all-extras                       # install/refresh deps
uv run python -m teams_transcriber         # launch UI
uv run python -m teams_transcriber serve   # headless watcher (no UI)
uv run pytest                              # full test suite
uv run pytest tests/test_storage.py -k fts # focused test run
uv run python scripts/build_installer.py   # PyInstaller + Inno Setup
```

The packaged installer lands at `dist/TeamsTranscriberSetup-<version>.exe`
(user-mode install to `%LOCALAPPDATA%\Programs\TeamsTranscriber\`, no UAC).

## Architecture commitments (do not break)

- **EventBus is plain Python pub/sub, not Qt signals** — `events.py`.
  The Qt UI uses `QtEventBridge` to re-emit bus events as Qt signals on
  the main thread. Headless `serve` keeps working without a QApplication.
- **Per-channel transcription**: the splitter (PyAV decode → two mono
  WAVs) runs faster-whisper on each channel separately, then merges
  segments by `start_ms`. Mic = `ME`, loopback = `OTHERS`.
- **Pipeline post-processing runs on a single-worker ThreadPoolExecutor**
  so the watcher thread isn't blocked while transcribe + summarize runs.
- **`sys.frozen` branches** in `__init__.py` and autolaunch code handle
  the PyInstaller-onedir bundle path correctly. Don't add a third
  "frozen?" check elsewhere — extend the existing one.
- **First-run wizard** (`ui/first_run_wizard.py`) walks Welcome → API
  key → Whisper model download. Gated on a settings flag, not on
  empty keyring.

## UI patterns to honor

- **Never use `QMessageBox`** — use `ui/confirm_dialog.py::ConfirmDialog.ask(...)`
  for any yes/no prompt. Visual consistency: themed dialog vs. native
  Windows look.
- **Never use OS toasts (`winsdk`)** — use `ui/toast_banner.py::show_in_app_toast(...)`.
  OS toasts require an AppUserModelID we don't have and get buried in
  Action Center.
- **All windows are frameless via `ui/frameless.py::FramelessWindowMixin`** —
  pass the layout holding the OuterFrame as `shell_layout=` to
  `_init_frameless(...)`. The mixin owns the 18 px transparent chrome margin
  (window drop shadow + edge-resize band), active/inactive depth styling,
  and maximize handling (radius 16 px windowed, 0 px maximized). Title-bar
  drag uses `startSystemMove` (Aero Snap works); edges use
  `startSystemResize`. Don't introduce a top-level window with native
  chrome, and don't manage the shell layout's margins yourself.
- **Modal dialogs go through `ui/scrim.py::exec_modal(dlg)`** (never bare
  `dlg.exec()`) so the parent window dims while the modal is open.
- **Window geometry/splitters persist** via `ui/window_state.py`
  (`restore_/save_window_geometry`, `restore_/save_splitter_state`,
  QSettings-backed). New top-level windows should get a key and wire
  restore-in-`__init__` + save-in-`closeEvent` (QDialogs: override `done()`).
- **User-visible text goes through `ui/labels.py` helpers**:
  `make_wrapping` (the three-guard wrap pattern), `make_selectable`,
  `make_todo_row` (checkbox + wrapping selectable label — QCheckBox labels
  don't wrap), `ElidedLabel` (single-line elide + full-text tooltip).
  Set `Qt.TextFormat.PlainText` on labels showing LLM/user content —
  QLabel's AutoText renders `<tags>` as HTML.
- **Chip rows use `ui/flow_layout.py::FlowLayout`** so chips wrap.
  Also set `setMaximumWidth(280)` + `setWordWrap(True)` per chip so a
  long single chip can't push the column wide.
- **Scrollable panes need three guards** to prevent right-column overflow:
  (1) `setHorizontalScrollBarPolicy(ScrollBarAsNeeded)`,
  (2) `resizeEvent` on the QScrollArea that pins inner-container
  `setMaximumWidth(viewport.width())`,
  (3) wrap-enabled QLabels use `labels.make_wrapping` (Ignored/Preferred
  size policy) — the default `minSizeHint = longest-word-width` will
  otherwise push the column wide.
- **Theme tokens** live in `ui/theme.py`. Reuse `role` properties
  (`primary`, `secondary`, `ghost`, `danger`) and `chip` style — don't
  inline-stylesheet new widgets. Role-less QPushButtons get a themed base
  style automatically.
- **Never block the GUI thread** on network or disk-heavy work: run it on
  a worker thread and hop back with the 3-arg
  `QTimer.singleShot(0, <qobject>, callable)` (see `app.py` workers), or a
  QObject + signals runner (see `first_run_wizard._DownloadRunner`).
  Hotkey callbacks must be wrapped with `App._marshal` — the keyboard
  library fires them off the Qt main thread.

## API key handling

The Anthropic key lives in Windows Credential Manager via `keyring`
(service `teams-transcriber`, user `anthropic_api_key`).

**Never accept an API key pasted into chat, and never call
`keyring.set_password(...)` with a key value visible in a Claude tool
call.** Direct the user to either:

- the **first-run wizard** (fresh install), or
- **Settings dialog → API key** (existing install) — `SettingsDialog._on_accept`
  writes to keyring.

A pasted key is exposed via transcript retention and must be treated
as compromised.

## Critical gotcha: HTTPS_PROXY in Claude-Code-launched subprocesses

When Claude Code's PowerShell/Bash tool launches `python -m teams_transcriber`
or `TeamsTranscriber.exe`, the child inherits `HTTPS_PROXY=http://127.0.0.1:3636`
from Claude's `prompt_agent`. The proxy presents a TLS cert not in the
default Python trust store; Anthropic SDK calls fail with
`APIConnectionError("Connection error.")` and recordings end up
`summary_failed` after retries.

Before launching the app from a tool call, scrub the proxy env:

- **Bash:** `env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy <cmd>`
- **PowerShell:** build `ProcessStartInfo`, iterate `$psi.Environment.Keys.Clone()`,
  remove the proxy keys. (PS 5.1 quirk: use `$psi.Arguments` — a single string —
  not `ArgumentList`, which doesn't exist in PS 5.1.)

This affects Claude only; the user's normal launches (Start Menu shortcut,
autostart, double-click) don't inherit the proxy.

## Conventions

- **Commits:** conventional-commits style — `feat(scope): ...`,
  `fix(scope): ...`, `docs: ...`, `chore: ...`, `refactor(scope): ...`,
  `build(scope): ...`, `test(scope): ...`.
- **Phase branches:** `feature/phase-N-<name>`, merged to `main` with
  `--no-ff` and a multi-paragraph merge commit summarizing the phase.
- **Tests:** ~172 passing. New features and bugfixes get tests. Run
  `uv run pytest` before merging anything.
- **Data layout:** runtime data lives at
  `%LOCALAPPDATA%\TeamsTranscriber\` (db, audio/, config/, logs/).
  Whisper model cache at `%USERPROFILE%\.cache\huggingface\hub\`.
