# Phase 4 — Manual Verification Checklist

Run these after a fresh `python scripts/build_installer.py`.

## Build

- [ ] `uv run python scripts/build_installer.py` completes without errors.
- [ ] Final line reports `Installer: dist\TeamsTranscriberSetup-<v>.exe (~XYZ MB)`.
- [ ] Smoke-test step prints `smoke-test ok` (or just exits 0 in windowed mode).

## Installer flow

- [ ] Run `dist\TeamsTranscriberSetup-<version>.exe` on a Windows account
      that has never installed Teams Transcriber.
- [ ] No UAC prompt during install.
- [ ] Install completes; Start Menu entry "Teams Transcriber" exists.
- [ ] Desktop shortcut exists (if Tasks/desktopicon was checked).
- [ ] App launches from Start Menu.
- [ ] No console window flash on launch.

## First-run wizard

- [ ] Welcome page shows on first launch.
- [ ] API key page accepts a paste of a real `sk-ant-...` key.
- [ ] Skipping the API key still allows finishing the wizard.
- [ ] "Start on login" checkbox state persists.
- [ ] Model download starts when advancing to page 3 (or completes fast
      if HF cache already has the model).
- [ ] Finish closes the wizard; main app opens to history.
- [ ] `%LOCALAPPDATA%\TeamsTranscriber\config\.first-run-complete` exists.
- [ ] Next launch SKIPS the wizard.

## Functional pipeline (frozen exe)

- [ ] Start a Meet Now in Teams; meeting is auto-detected.
- [ ] Recording row appears in history with status "Recording".
- [ ] Ending the meeting triggers transcribe → summarize.
- [ ] Summary populates with todos / actions / topics.
- [ ] Manual recording (hotkey) works.

## Autolaunch (frozen mode)

- [ ] Reboot the machine.
- [ ] Teams Transcriber appears in the system tray automatically.
- [ ] No console window flash.
- [ ] `reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v TeamsTranscriber`
      shows the .exe path with **NO** `-m teams_transcriber` argument.

## Uninstall

- [ ] Settings → Apps → Teams Transcriber → Uninstall removes the install dir.
- [ ] User data under `%LOCALAPPDATA%\TeamsTranscriber\` is PRESERVED
      (recordings, db). Reinstalling restores them.
- [ ] Autolaunch entry: removed manually if needed (uninstaller doesn't
      currently touch HKCU\...\Run — known limitation; reinstall fixes).

## Signed build (only if cert configured)

- [ ] Installer triggers no SmartScreen warning on a clean machine.
- [ ] `signtool verify /pa dist\TeamsTranscriberSetup-<v>.exe` succeeds.
