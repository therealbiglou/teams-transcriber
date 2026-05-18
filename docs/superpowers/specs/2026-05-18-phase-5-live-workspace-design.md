# Phase 5 — Live Workspace & Hotkeys

**Date:** 2026-05-18
**Status:** Approved (Brian, 2026-05-18)
**Drives plan:** `docs/superpowers/plans/2026-05-18-phase-5-live-workspace.md` (to follow)

## Goals

1. Show transcription as it happens, in a dedicated **Workspace window** that puts
   manual notes (left, 70 %) next to a live transcript pane (right, 30 %).
2. Replace the two existing transient surfaces — `NotesWindow` (rich-text dialog)
   and `TranscriptView` (transcript dialog) — with this single Workspace window.
   It serves both live recordings and past-recording editing.
3. Make the live transcript persist incrementally to SQLite as it arrives, so
   nothing is lost if the app crashes mid-meeting.
4. Show the **final transcript inline** under the summary in the main app, so
   the standalone transcript dialog goes away entirely.
5. Add **full keyboard-shortcut customization** for the three actions Brian
   actually wants on hotkeys: toggle recording, open the workspace, toggle
   detection-paused.

## Non-goals (deferred)

- Auto-positioning the Workspace next to the Teams window (Phase 5.5 polish).
- Confidence shading per segment or in-line transcript edit. Brian's correction
  flow is via manual notes — the summarizer already receives them and rewrites
  jargon when producing the final summary.
- Search within the live transcript pane (the global search bar already covers
  this once segments persist).
- Long-meeting transcript chunking for transcripts > 600k chars. Still a
  separately tracked open item.

## Background — what changes vs. today

| Concern                | Today                                       | Phase 5                                            |
|------------------------|---------------------------------------------|----------------------------------------------------|
| Transcription timing   | Runs once after `meeting_ended`, batch.     | Streams during meeting; tail processed at end.     |
| Notes UI               | `NotesWindow` dialog, opened from toast/tray.| Workspace window (left pane).                     |
| Past-recording notes   | Same `NotesWindow`.                          | Workspace window in read-only-transcript mode.    |
| Transcript view        | Separate `TranscriptView` dialog button.    | Inline in summary pane (main app) + Workspace pane.|
| Hotkeys                | Only `ctrl+alt+r` (toggle record) wired.    | 3 keys wired + editable in Settings.              |

## Architecture

### Components

**`Recorder` (modified)** — accepts an optional `audio_chunk_callback:
Callable[[np.ndarray], None]`. The recorder calls it inside the capture loop
right after `OpusWriter.write_chunk(chunk)`. Calling the callback is wrapped in
`try/except` so a slow or failing callback can't crash recording. If the
callback is `None`, the recorder behaves exactly as today.

**`LiveTranscriber` (new, `src/teams_transcriber/live_transcriber.py`)** — owns
the streaming faster-whisper inference. Architecture:

- One faster-whisper instance, **not two**. Two concurrent instances on the
  3050 Ti's 4 GB VRAM is unsafe given Teams uses ~1 GB during a call.
- Two per-channel rolling buffers (`bytearray` of float32 mono PCM at 16 kHz).
- A single worker thread that **strictly alternates between channels**: process
  ME's buffer, then OTHERS' buffer, then ME, then OTHERS — never the same
  channel twice in a row, so neither channel can starve the other. A processing
  pass fires when either (a) the next-in-line channel has ≥ 10 s of audio
  buffered, or (b) at least 15 s have elapsed since that channel's last pass
  (whichever comes first — the timer condition guarantees latency under low
  audio activity). On each pass: copy the next-in-line channel's buffer to a
  temp WAV, run `model.transcribe(...)` against it, emit segments tagged with
  that channel, clear the consumed range from the buffer.
- Expected end-to-end latency: 10–20 s from speech to on-screen segment.
  Tunable via `settings.transcription.live_flush_interval_ms` (default 10000)
  and `settings.transcription.live_max_wait_ms` (default 15000).
- VAD enabled (same as today): avoids spending GPU time on silence and avoids
  emitting empty segments.
- Each emitted `TranscriptSegment` is persisted to `transcript_segments`
  immediately via `TranscriptRepo.append()` (single-segment write already exists),
  and published on the bus as `LiveSegmentAvailable(recording_id, segment)`.

The `LiveTranscriber` is started by `Pipeline._handle_meeting_started` (or the
manual-start flow) before/alongside `Recorder.start`, and stopped from
`Pipeline._handle_meeting_ended` after `Recorder.stop`.

**`LiveSegmentAvailable` event (new)** — added to `events.py`:

```python
@dataclass(frozen=True, slots=True)
class LiveSegmentAvailable:
    recording_id: int
    segment: TranscriptSegment
```

Re-emitted by `QtEventBridge` on the main thread for the UI.

**`Transcriber` (modified)** — `transcribe(recording_id)` becomes the *finalizer*
rather than the *worker*:

- If `transcript_segments` already covers ≥ 95 % of the recording's duration
  (i.e. the `LiveTranscriber` covered the meeting), skip Whisper re-runs and
  advance status directly to `SUMMARIZING`.
- Else, fall back to the current behavior (split the Opus to two mono WAVs,
  run Whisper on each, merge by start_ms, persist). This is the recovery path
  when `LiveTranscriber` errored mid-meeting (CUDA OOM, audio drop, exception).
- Either way: emit `TranscriptionComplete` and let the Pipeline continue.

**`WorkspaceWindow` (new, `src/teams_transcriber/ui/workspace_window.py`)** —
the dedicated window. Frameless, themed, follows the same `MainWindow` /
`TitleBar` / `QGraphicsDropShadowEffect` pattern (16 px corner radius when
windowed, 0 when maximized — same as the main window). Default size ~1100×700.

Layout:

```
┌──────────────────────────────────────────────────────────────────────┐
│  [Title]  Potter // House of Blues - Reception     ● Recording  □ X │  ← titlebar
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──── Notes (70 %) ─────────────────┐  ┌─ Transcript (30 %) ────┐  │
│  │  [B] [I] [U] [• list] [1. list]   │  │  ME   00:12             │  │
│  │ ─────────────────────────────────  │  │   Hey Jennifer, can we…│  │
│  │ Jennifer to send signed contract   │  │                          │  │
│  │ by Friday. Confirm room block      │  │  OTHERS 00:18           │  │
│  │ count is 280 not 320. Note —       │  │   Yeah, I'll send the   │  │
│  │ "fiberglass" was misheard as       │  │   updated draft tonight…│  │
│  │ "pierglass" a few times.           │  │                          │  │
│  │                                    │  │  ME   00:34             │  │
│  │                                    │  │   …                      │  │
│  └────────────────────────────────────┘  └──────────────────────────┘ │
│                                                                      │
│                              [Stop recording]   [Close]              │
└──────────────────────────────────────────────────────────────────────┘
```

- **Left (notes pane, 70 %)** — extracted `NotesEditor` widget (the rich-text
  body currently embedded inside `NotesWindow`). Same formatting toolbar
  (Bold / Italic / Underline / Bullet / Numbered list). Auto-save debounced at
  1 s after the last keystroke, plus a save-on-blur and save-on-close guard
  (today `NotesWindow` only saves on close — Phase 5 makes this safer for live
  use). Persists to `recordings.manual_notes` (existing column).
- **Right (transcript pane, 30 %)** — `LiveTranscriptView` widget. A
  `QListWidget` (or `QListView` over a model — see "Open question" below) of
  segments. Each row: channel badge (ME = emerald pill, OTHERS = neutral pill),
  `mm:ss` timestamp, segment text. New segments append to the end. Auto-scrolls
  to the bottom **only when the scroll position is already at the bottom**;
  if the user has scrolled up, auto-scroll pauses until they scroll back down.
  Selectable / copyable text. No editing.
- **Titlebar** controls: meeting title, recording-state indicator (red ● when
  recording, gray ◯ when finished), always-on-top toggle (pin icon),
  minimize / close. Use the same `TitleBar` pattern as `MainWindow` but with a
  shorter button set.
- **Footer** controls: "Stop recording" (only when live), "Close".
- **Status banner** (above footer, visible only when relevant): "Live
  transcription paused — will catch up after meeting." Shown when
  `LiveTranscriptionDegraded` fires.
- **Past-recording mode** — when opened for a recording that's already
  finished, the right pane is populated once from `TranscriptRepo.list_for_recording`,
  no live updates, and the "Stop recording" button is hidden.

**`SummaryPane` (modified, `src/teams_transcriber/ui/summary_pane.py`)** —
gains a collapsible "Transcript" section below the existing summary content.
Closed by default to keep the pane fast to scan. When opened, renders the
recording's transcript segments inline using the same `LiveTranscriptView`
widget (read-only). Replaces the current "View transcript" button + dialog;
the standalone `TranscriptView` class is deleted.

**`HotkeyManager` (modified)** — gains a `reload(hotkeys: dict)` method so
the Settings dialog can apply changes without restarting the app. Internally,
`stop()` then `register()` the new bindings.

**`SettingsDialog` (modified, `src/teams_transcriber/ui/settings_dialog.py`)** —
new "Shortcuts" section with three editable rows:

| Action                   | Default        | Key       |
|--------------------------|----------------|-----------|
| Toggle recording         | `ctrl+alt+r`   | `toggle_manual_recording` |
| Open workspace           | `ctrl+alt+n`   | `open_workspace`          |
| Pause/unpause detection  | `ctrl+alt+p`   | `toggle_pause_detection`  |

Each row: a label, a captured-hotkey input that records the next key combination
the user presses, a "Reset to default" mini-button. Validation: refuse blank
strings; warn (but allow) collisions across the three fields. Save persists into
`settings.hotkeys.*` and calls `HotkeyManager.reload(...)`.

### Data flow — live recording

```
soundcard ─── RealAudioSource ───────────► Recorder ─── OpusWriter ──► .opus
                                              │
                                              ├─► audio_chunk_callback ─► LiveTranscriber
                                              │                              │
                                              │                              ├─► faster-whisper (single instance, round-robin per channel)
                                              │                              │
                                              │                              ├─► TranscriptRepo.append(segment)   ──► SQLite
                                              │                              │
                                              │                              └─► bus.publish(LiveSegmentAvailable)
                                              │                                      │
                                              │                                      ▼
                                              │                              QtEventBridge (main thread)
                                              │                                      │
                                              │                                      ▼
                                              │                              WorkspaceWindow.LiveTranscriptView
                                              │
                                              └─► bus.publish(RecordingStarted) ─► tray / toast / workspace auto-open
```

### Data flow — meeting end

1. `MeetingWatcher` emits `meeting_ended`.
2. `Pipeline` calls `Recorder.stop()` (closes Opus file, persists final state).
3. `Pipeline` calls `LiveTranscriber.flush_and_stop()` — drains remaining
   in-buffer audio through Whisper, persists any final segments, joins the
   worker thread.
4. `Pipeline` calls `Transcriber.transcribe(recording_id)` — the new
   "finalize-or-recover" entrypoint described above. In the happy path it
   simply verifies coverage and advances to `SUMMARIZING`.
5. `Summarizer` runs as today; the Workspace transitions to "finished" mode
   (red dot turns gray, stop button hides).

### Manual recordings

Same flow, kicked off via the tray "Start recording" item or hotkey instead of
`MeetingWatcher`. The Workspace can be opened manually via `ctrl+alt+n` at any
time — when there's no active recording, it opens for the most recently
completed recording instead (and the title bar reflects that).

### Hotkey behaviors

- **`toggle_manual_recording`** — same as today: start a manual recording if
  idle, stop if recording.
- **`open_workspace`** — open (or raise + focus) the Workspace window for the
  active recording. If no active recording, open for the most-recent
  completed recording. If there are zero recordings, no-op + show a toast
  ("Nothing to show yet").
- **`toggle_pause_detection`** — flip `MeetingWatcher.set_paused(...)`. Visible
  via tray icon state (existing).

## Error handling

| Failure mode                                | Behavior                                                                          |
|----------------------------------------------|-----------------------------------------------------------------------------------|
| `LiveTranscriber` raises mid-meeting (CUDA OOM, model error, etc.) | Log + publish `LiveTranscriptionDegraded`. Workspace shows banner. Recorder keeps writing Opus. Post-meeting Transcriber falls back to the current "split + batch transcribe" path so we still get a full transcript. |
| Audio chunk callback raises (defensive)      | Caught + logged inside `Recorder._run()`. Recording continues uninterrupted. After 3 consecutive callback errors in a 30 s window, callback is auto-disabled and `LiveTranscriptionDegraded` fires. |
| `TranscriptRepo.append()` raises (db lock)   | Retry once with 100 ms back-off; on second failure, fire `LiveTranscriptionDegraded`. Segment buffered in memory until next successful write. |
| Notes auto-save raises                       | Log only. Editor keeps the in-memory text. Next save attempt retries. Save-on-close is the safety net. |
| Workspace closed during recording            | Recording continues. Reopen via `ctrl+alt+n` or tray menu. The right pane catches up by reading persisted segments at mount time, then attaches to the live stream. |
| Hotkey reload fails (e.g. invalid string)    | Settings dialog shows inline error, doesn't persist the change. Existing hotkeys remain active. |

## Testing

### Unit tests

- **`LiveTranscriber`** with a mock model: feed scripted PCM chunks → assert
  segments emitted in order, persisted to the repo, published on the bus.
  Cover the round-robin scheduler (fair distribution between channels under
  asymmetric load) and the flush-on-stop path (no buffered audio left behind).
- **Recorder audio-chunk callback** — verify it's called per chunk; verify a
  raising callback doesn't crash the recorder loop; verify the auto-disable
  after N failures.
- **`Transcriber.transcribe()` finalize-or-recover** — case 1: segments cover
  the recording (live succeeded) → skip Whisper, advance to summarizing. Case
  2: no segments / partial coverage → fall back to batch transcription.
- **`HotkeyManager.reload()`** — register A, reload with B → only B fires.
- **`Settings.update_hotkeys()`** — round-trip save / load; default-fallback
  when a key is missing.

### Integration tests

- Pipeline end-to-end with `FakeAudioSource` and a mock Whisper model:
  recorder starts → live segments persist in real time → meeting ends → no
  audio left in the live buffer → summary fires. Use `bus` subscription to
  count `LiveSegmentAvailable` events.
- "Live failed, batch recovers" — mock `LiveTranscriber` raises on chunk 3 →
  recording continues → at meeting-end, `Transcriber.transcribe()` runs the
  full batch path and produces segments equal to the no-failure run.

### UI tests (offscreen Qt)

- `WorkspaceWindow` mounted with a recording id: subscribes to bridge,
  appends segments on `LiveSegmentAvailable`, ignores events for other
  recording ids.
- Notes auto-save: type → wait > 1 s → assert `recordings.manual_notes`
  contains the new HTML.
- Scroll-pause behavior: simulate scroll-up → new segment arrives → assert
  the viewport doesn't jump. Simulate scroll back to bottom → next segment →
  assert it auto-scrolls.
- Settings "Shortcuts" round-trip: change → save → load settings → asserts.

## Open / decided design points

1. **`QListWidget` vs. model-backed `QListView` for the transcript pane.**
   Decision: `QListWidget` for v1 — meetings produce O(few-hundred) segments,
   no need for the model overhead yet. Revisit if we ever hit performance
   problems.
2. **Always-on-top default on the workspace.** Decision: off by default; user
   toggles per session. We don't persist the toggle.
3. **Workspace auto-opens on recording start?** Decision: yes for manual
   recordings (you intentionally clicked record — you want the workspace).
   No for auto-detected meetings (Teams is already in focus — don't steal it).
   The toast still appears with an "Open workspace" action that opens it on
   demand.
4. **Stop-recording hotkey separate from toggle?** Decision: no. Toggle works
   for both directions. Keeps the hotkey set small.

## Phase-4 cleanup carried alongside

These aren't part of Phase 5's scope but are easy to fold in:

- Retry summaries for `summary_failed` rows #3 and #4 (one-off CLI command,
  run from a proxy-scrubbed env).
- Replace the placeholder `installer/icon.ico` with a finalized icon (still
  open from Phase 4). Skip if Brian doesn't have one ready — leave for a
  Phase 5.5.

Code signing and the full Phase-4 manual-verification checklist remain open
but are independent of Phase 5.

## Risks

| Risk                                                     | Mitigation                                                                                       |
|----------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| 3050 Ti VRAM exhaustion during a Teams call              | Single Whisper instance; round-robin; fallback to post-meeting batch transcription on OOM.        |
| Live transcription latency feels too long (>20 s)        | The 10-s flush is tunable in `settings.transcription.live_flush_interval_ms`. Default 10000.      |
| `keyboard` library hotkey-rebind crashes on Windows      | Wrap `add_hotkey` / `remove_hotkey` in try/except (already done); fall back to defaults on error. |
| Frameless `WorkspaceWindow` always-on-top fights Windows | Use `Qt.WindowStaysOnTopHint` toggle the standard way; verified pattern already in toast banner.  |
| Notes lost on crash                                      | Auto-save debounced 1 s + save-on-blur; persist to existing `recordings.manual_notes`.            |
