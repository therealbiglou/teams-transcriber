# Phase 7 Manual Verification

## Build

- [ ] `dist/TeamsTranscriberSetup-0.4.0.exe` exists.
- [ ] Installer size is ~250 MB (compare to Phase 6's 995 MB).
- [ ] Bundle inspection: `dist/TeamsTranscriber/_internal/` does NOT contain
      an `nvidia/` directory.

## Fresh-machine install path

On a Windows machine that has never had the app installed and has no cached
GPU runtime:

- [ ] Run the installer. Installs cleanly to `%LOCALAPPDATA%\Programs\TeamsTranscriber\`.
- [ ] Launch the app. First-run wizard opens.
- [ ] Walk: **Welcome → API key → GPU runtime → Whisper model**.
- [ ] Click Next on the GPU runtime page. Download begins (~700 MB total).
      Progress bar advances; label updates with the current package name
      (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, `nvidia-cuda-nvrtc-cu12`).
- [ ] Download completes. "GPU runtime ready." appears. Next.
- [ ] Whisper model page downloads ~3 GB on first inference. Finish.
- [ ] App opens. In Settings → Audio, pick a real mic.
- [ ] Press `ctrl+alt+r` to start a manual recording. Speak for 10 s.
      Stop. Summary fires (this confirms the runtime + model both work).

## Upgrade path (cache persists)

- [ ] Build a follow-up version (e.g., bump to 0.4.1 with a trivial change).
- [ ] Install over 0.4.0 (no uninstall).
- [ ] Launch. First-run wizard does NOT appear (marker file still present).
- [ ] GPU runtime registers from the existing cache. App starts normally.
- [ ] `%LOCALAPPDATA%\TeamsTranscriber\runtime\nvidia\` is unchanged from before.

## Network failure handling

- [ ] Disconnect from the internet. Wipe the runtime cache:
      `Remove-Item "$env:LOCALAPPDATA\TeamsTranscriber\runtime\nvidia\*" -Recurse -Force`
- [ ] Launch the app. Wizard reappears. On GPU runtime page, click Next.
- [ ] Error message appears (network failure).
- [ ] Reconnect. Re-launch the app. Wizard retries the download and succeeds.

## CLI mode without runtime

- [ ] Wipe the runtime cache.
- [ ] Run `TeamsTranscriber.exe serve` from a fresh PowerShell.
- [ ] Exits with code 2 + clear message:
      "GPU runtime not installed. Launch the GUI once to set it up
      (it'll download ~700 MB of NVIDIA libraries)."
- [ ] Launch the GUI once. Wizard's runtime page populates the cache.
- [ ] `TeamsTranscriber.exe serve` now starts normally.

## Regression check

- [ ] Phase 5 + Phase 6 features still work: live transcription toggle,
      audio device selection, WASAPI Teams detection, workspace UI,
      hotkey rebinding, settings dialogs.

## Bundle inspection notes

- The installer's compressed size (Inno Setup LZMA2) is roughly 30-40 % of
  the uncompressed `dist/TeamsTranscriber/` tree. With NVIDIA gone, the
  uncompressed tree should be ~600 MB → installer ~250 MB.
- If the build comes in larger than expected, inspect `dist/TeamsTranscriber/_internal/`
  for any remaining `nvidia/` paths. The PyInstaller spec's `_is_nvidia_path`
  filter should catch them; if it doesn't, add path patterns and rebuild.
