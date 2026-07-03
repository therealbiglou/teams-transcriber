# Phase 5 Manual Verification

Run from a clean PowerShell (no Claude proxy env — these tests need real Anthropic API calls for the summary step). Each box must be ticked before merging to `main`.

```powershell
uv run python -m teams_transcriber
```

## Live workspace — manual recording

- [ ] Open the app. Press `ctrl+alt+r`.
- [ ] WorkspaceWindow appears (frameless, rounded corners, drop shadow).
- [ ] Left pane is the notes editor with formatting toolbar (Bold / Italic / Underline / Bullet / Numbered list / Clear).
- [ ] Right pane is empty initially, with the rounded white card border visible.
- [ ] Speak into the mic for ~15 s; play audio out of the speakers for the same time.
- [ ] Within ~20 s of speech, segments appear on the right with channel badges
      (ME — emerald pill for mic, OTHERS — neutral pill for loopback).
- [ ] Type notes in the left pane while transcription continues. Both panes
      stay responsive — no UI freeze.
- [ ] Scroll the right pane up to read older content. Confirm auto-scroll
      pauses (newer segments arrive but the view doesn't jump).
- [ ] Scroll back to the bottom — confirm auto-scroll resumes.
- [ ] Click "Stop recording" in the footer. The titlebar's red dot turns gray.
      The "Stop recording" button disappears.
- [ ] Title bar's pin button (📌) toggles always-on-top. Verify by clicking it
      then dragging another window over the workspace — workspace stays above.
      Click again to release.

## Live workspace — Teams meeting

- [ ] Start a real Teams call (Meet Now).
- [ ] Wait for auto-detection. Recording starts (toast appears).
- [ ] Workspace does NOT auto-open (Teams should keep focus).
- [ ] Click the toast's "Open workspace" button. Workspace opens.
- [ ] End the Teams call. Recorder finalizes. Workspace's red dot turns gray,
      stop button disappears.
- [ ] Wait for summary. Tray icon returns to idle. Toast says "Summary ready".

## Hotkeys

- [ ] `ctrl+alt+r` while idle → starts manual recording + opens workspace.
- [ ] `ctrl+alt+r` while recording → stops recording (workspace stays open
      but transitions to finished mode).
- [ ] `ctrl+alt+n` → opens workspace for the currently-active recording, or
      most recent finished one if idle.
- [ ] `ctrl+alt+p` → toast: "Detection paused". Start a Teams call and
      confirm no recording starts. Press again → toast: "Detection resumed".
- [ ] Open Settings → Shortcuts tab. Change "Open workspace" to `ctrl+shift+w`.
      Save. Press the new shortcut → workspace opens. Press `ctrl+alt+n` →
      nothing (old binding is gone). Reset via the Reset button.

## Past-recording workspace

- [ ] Click an older recording in the history list.
- [ ] Main app's summary pane shows the summary AND an inline collapsible
      "Transcript" section. Click "Show" — segments render with channel badges.
      Click "Hide" — section collapses.
- [ ] Open the workspace for this older recording (Notes button on the
      summary). Workspace opens in past mode: red dot stays gray, stop button
      hidden, right pane shows the full transcript (read-only), left pane
      shows existing notes (editable).
- [ ] Edit notes — wait ~2 s — close the workspace — reopen — notes persisted.

## Notes auto-save

- [ ] Open workspace for an active recording. Type some notes.
- [ ] Wait ~2 s. Close the workspace (X button on titlebar). Reopen via
      `ctrl+alt+n`.
- [ ] Notes are intact (the debounced auto-save fired during typing AND
      the save-on-close ran).

## Live failure recovery

- [ ] Simulate a live-transcription failure: temporarily rename the Whisper
      cache directory at `%USERPROFILE%\.cache\huggingface\hub\`, then start
      a recording (the model load will fail mid-meeting). Confirm:
   - Recording continues (Opus file still written).
   - At meeting end, the post-meeting Transcriber runs the legacy batch
     path automatically (no segments yet means coverage < 95 %).
   - Summary eventually arrives.
- [ ] Restore the Whisper cache directory.

## Final transcript in summary pane

- [ ] After a meeting summarizes, the SummaryPane's "Transcript" collapsible
      shows all merged segments in order. Channel labels are correct.
- [ ] There is NO separate "Transcript" dialog button anywhere — the inline
      section is the only way to view the transcript from the summary surface.

## Regressions check

- [ ] Existing flows still work: recording history list, search, delete
      recording, export summary, copy markdown.
- [ ] Tray icon still cycles IDLE / RECORDING / PROCESSING / ERROR correctly.
- [ ] First-run wizard still works on a clean install (delete
      `%LOCALAPPDATA%\TeamsTranscriber\config\settings.json` and the
      `first_run.marker` to retest).
- [ ] Auto-launch toggle in Settings still adds/removes the registry entry.
