# Phase 3 UI Manual Verification Checklist

Run after Phase 3 lands. Each item is a real-world sanity check that the mocked
tests cannot catch.

## Launch + first-run

- [ ] `uv run python -m teams_transcriber` launches the UI without errors.
- [ ] Window is frameless with rounded corners (~16 px).
- [ ] Title bar shows "Teams Transcriber" + min/max/close buttons.
- [ ] Background is warm off-white; sidebar is slightly tinted.
- [ ] Tray icon visible in the system tray (right side of taskbar).

## Tray icon

- [ ] Left-clicking the tray icon opens the main window.
- [ ] Right-clicking shows the menu with: Open, Start manual recording, Stop, Pause auto-detection, Settings, Quit.
- [ ] When recording, icon turns red.
- [ ] When transcribing/summarizing, icon turns amber.
- [ ] "Quit" actually exits the process (no zombie Python process).

## Sidebar + history list

- [ ] Recordings display newest-first, grouped by date bucket (Today / Yesterday / This week / Earlier).
- [ ] Each card shows: title, date+duration, one-line summary (if present), todo-count chip (if > 0).
- [ ] Sidebar buttons filter the list (All / Today / Yesterday / This week / Earlier / Manual / Failed).
- [ ] Clicking a card displays its summary on the right.

## Summary detail pane

- [ ] All summary sections render (Summary, My todos, Action items, Key decisions, Follow-ups, Topics).
- [ ] My-todo checkboxes can be toggled and persist after restarting the app.
- [ ] "View transcript" opens a scrollable transcript dialog with ME/OTHER labels and per-segment timestamps.
- [ ] "Copy markdown" copies a clean markdown summary to the clipboard (paste into Notepad to verify).
- [ ] "Export…" opens a save dialog and writes a `.md` (or `.txt`) file to the chosen location.

## Search

- [ ] Typing in the search box filters the history list after a short pause (~250 ms).
- [ ] Search matches text in titles and one-line summaries.
- [ ] Clearing the search shows all recordings again.

## Settings dialog

- [ ] Opens from tray menu and main window menu actions.
- [ ] All 5 tabs (General / Audio / Detection / Transcription / AI) render and switch cleanly.
- [ ] Detection tab lets you add and remove title patterns.
- [ ] Audio retention spinner accepts 0–3650 days.
- [ ] API key field is password-masked; placeholder shows the stored key prefix if present.
- [ ] Saving the dialog writes `settings.json` AND updates Windows Credential Manager (if a new key was typed).

## Hotkeys

- [ ] `Ctrl+Alt+R` toggles manual recording (tray icon flips between idle/red).

## Auto-launch

- [ ] When General → Auto-launch is checked and OK is pressed, `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\TeamsTranscriber` is created.
- [ ] When unchecked, the entry is removed.
- [ ] On next Windows reboot, the app starts automatically.

## Detection (smart-fallback)

- [ ] Start a "Meet now" Teams meeting → detected within ~5 s without manually adding a pattern.
- [ ] Start a scheduled Teams meeting with an arbitrary subject (e.g. `Q4 strategy`) → detected without adding a pattern.
- [ ] Calendar, Chat, Activity, etc. views of Teams do NOT trigger detection.

## Full pipeline (real meeting)

- [ ] Join a Teams meeting; tray icon turns red; toast appears reading "Recording started".
- [ ] Leave the meeting; tray icon goes amber; meeting appears in history with "Transcribing" status.
- [ ] After processing finishes (≈ 1–2 min per 10 min of meeting), tray returns to idle and a "Summary ready" toast appears.
- [ ] Clicking the "Summary ready" toast opens the summary detail pane for the right recording.

## Soundcard warnings

- [ ] When recording a real meeting, the `serve` / UI log does NOT contain hundreds of "data discontinuity in recording" lines.
