# Phase 6 Manual Verification

Run from a clean PowerShell (no Claude proxy env). Tick each box before merging.

## Audio device selection

- [ ] Open Settings → **Audio** tab. Both dropdowns enumerate real devices on the machine.
- [ ] Each dropdown has "Use Windows default" as the first option.
- [ ] Pick a specific microphone (not the default). Save. Restart the app. Settings remembers the choice.
- [ ] Press `ctrl+alt+r` to start a manual recording. Confirm via `mmsys.cpl` that the chosen mic shows the "in use" green meter.
- [ ] Stop recording. Disconnect the saved mic (unplug USB, turn off Bluetooth headset, etc.). Press `ctrl+alt+r` again.
- [ ] Toast appears: "Saved microphone 'X' not connected — using Windows default. Choose a different device in Settings → Audio." Click "Open Settings". Settings opens directly on the Audio tab.
- [ ] Recording proceeds normally using the Windows default — no crash, summary fires at the end.

## WASAPI Teams detection

- [ ] In Settings → Detection, remove all title patterns so title-matching cannot fire.
- [ ] Start a Teams meeting. Auto-detection still fires (the WASAPI capture-session probe catches it). Toast: "Recording started".
- [ ] End the meeting. `MeetingEnded` fires; tray cycles to PROCESSING; eventually "Summary ready" toast.
- [ ] Open a Teams Chat conversation (no mic activity). Confirm auto-detection does NOT fire.
- [ ] Restore title patterns. Confirm hybrid detection still works (both signals available).

## `live_enabled` toggle

- [ ] Fresh install / new profile: Settings → Transcription → "Stream transcription during recording (experimental)" is **unchecked**.
- [ ] Start a manual recording. Workspace window opens; transcript pane shows the placeholder card: "Transcription will appear when the meeting ends."
- [ ] Talk for ~15s. The placeholder stays — no live segments appear.
- [ ] Stop recording. Wait for summary. Workspace's transcript pane auto-loads the final segments (placeholder disappears).
- [ ] Check the "Stream transcription" box. Save. Start another recording. The placeholder is gone; segments stream live (Phase 5 behavior).
- [ ] Uncheck the box. Save. Verify the next recording is back to placeholder mode.

## Error UX — no audio devices

- [ ] On a machine with no audio endpoints visible (or after disabling them in Device Manager): press `ctrl+alt+r`.
- [ ] Toast: "Recording failed" with body "No audio devices available — check Settings → Audio." Action button: "Open Settings".
- [ ] Click "Open Settings". Settings opens directly on the Audio tab.
- [ ] Tray icon flashes to ERROR state momentarily.
- [ ] Verify in the history list: no partial recording row was created. The pipeline cleanly refused to start.

## Regression checks

- [ ] Existing flows still work: recording history list, search, delete recording, edit notes, export summary, copy markdown, all hotkeys.
- [ ] Phase 5 workspace UI still works (with `live_enabled=True`): notes pane on the left, transcript pane on the right, always-on-top toggle in the titlebar, "Stop recording" button.
- [ ] First-run wizard on a clean install still completes successfully.
- [ ] Auto-launch toggle in General settings still adds/removes the registry entry.
