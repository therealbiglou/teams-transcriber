# Phase 6 — Detection & Audio Selection

**Date:** 2026-05-19
**Status:** Approved (Brian, 2026-05-19)
**Branch:** `feature/phase-6-detection-audio-selection`
**Drives plan:** `docs/superpowers/plans/2026-05-19-phase-6-detection-audio-selection.md` (to follow)

## Goals

Phase 6 is four small, independent improvements that collectively make meeting capture *actually start* and *capture from the right inputs*:

1. **Audio device selection in Settings.** Explicit microphone + system-audio (loopback) pickers, persisted by ID with name as fallback. Replaces the always-implicit "Windows default" used since Phase 2.5.
2. **WASAPI session-based Teams detection.** When `ms-teams.exe` / `Teams.exe` holds an active microphone capture session, you're in a meeting — regardless of how the window title reads. Hybrid: WASAPI is the detection source of truth; title patterns remain as a fallback and as the source of human-readable meeting names.
3. **`live_enabled` toggle** (default `False`). Reverts Phase 5's live streaming to opt-in. All Phase 5 code stays in place, just dormant. Re-enabling is one settings toggle away.
4. **Better error UX when no audio devices are available.** Recording-start failures surface as a clear toast with an "Open Settings" action, instead of an unexplained "nothing happens" state.

## Non-goals (deferred)

- CPU-only installer flavor.
- Cherry-picking cuDNN DLLs by GPU architecture.
- Long-meeting (>600 k char) transcript chunking.
- Speaker diarization, calendar correlation, To Do / Todoist export, auto-update.

## Background — what changes vs. Phase 5

| Concern                          | Phase 5                                              | Phase 6                                                                |
|----------------------------------|------------------------------------------------------|-------------------------------------------------------------------------|
| Audio capture devices            | Always Windows default mic + default speaker loopback. | Explicit, user-selectable in Settings → Audio. Fallback to default.    |
| Meeting detection                | Title pattern matching on visible Teams windows.    | Hybrid: WASAPI capture session for detection, title for naming.        |
| Live transcription default       | On.                                                  | Off. Toggleable in Settings → Transcription.                           |
| "No audio devices" failure       | Silent / cryptic recorder failure.                  | Explicit toast + tray ERROR state, with Settings deep-link.            |

## Architecture

### Components

**`Settings.audio_mic_device` / `Settings.audio_loopback_device` (modified, `config.py`).**
- Existing `mic_device` / `loopback_device` properties return `str | None`. The underlying storage changes from a string to a dict `{"id": str, "name": str} | None`.
- New typed properties: `audio_mic_device -> dict | None`, `audio_loopback_device -> dict | None`.
- `None` continues to mean "use Windows default".
- The existing `mic_device` / `loopback_device` properties remain (return `str | None`) for backwards compatibility; they return the `id` field of the new dict if set, else `None`.

**`RealAudioSource.from_settings(settings)` (new factory, `audio/source.py`).**
- Looks up the chosen mic per the ladder: saved `id` → saved `name` → Windows default → raise `NoAudioDevicesError`.
- Same ladder for loopback.
- When falling back from a saved device to the default (because the saved device isn't currently connected), the factory does *not* raise — it returns a working `RealAudioSource` and records the fallback so the caller can surface a `RecordingDeviceFallback` event.
- The existing `from_default_devices()` factory is kept as a thin convenience wrapper that calls `from_settings()` with a `None`-defaulted settings object.

**`NoAudioDevicesError` and `RecordingDeviceFallback` event (new, `audio/source.py` + `events.py`).**
- `NoAudioDevicesError(Exception)` — raised when neither saved nor default devices are available.
- `RecordingDeviceFallback(recording_id: int, channel: str, requested_name: str)` event — published when a recording starts but a saved device was unavailable and the default was used in its place.

**`wasapi_sessions.py` (new, `src/teams_transcriber/audio/wasapi_sessions.py`).**
- Single public function: `teams_active_capture_pids() -> set[int]`.
- Returns PIDs of `ms-teams.exe` (new Teams) and `Teams.exe` (classic Teams) that currently hold an active microphone capture session on any default-capture device.
- Implementation uses `pycaw` (new dependency, < 200 KB) to enumerate WASAPI capture-side sessions via COM. Filters by `AudioSessionState.Active` (state=1, not Inactive=2 or Expired=3).
- All COM errors and `pycaw` import errors are caught; the function returns an empty set on any failure (graceful degradation — the watcher falls back to title matching).

**`MeetingWatcher` (modified, `meeting_watcher.py`).**
- New constructor parameter: `audio_session_probe: Callable[[], set[int]] = teams_active_capture_pids`. Tests inject a stub.
- `_find_meeting_window` updated to a two-tier strategy:
  1. **WASAPI tier:** call `audio_session_probe()`. If non-empty: filter visible Teams windows to those PIDs, exclude nav views (existing denylist), pick the longest non-denylisted title. That window is the meeting.
  2. **Title-pattern tier (fallback):** the existing Phase 2 logic. Runs only when the WASAPI tier returns no candidate (probe returned empty, or no Teams windows match the PIDs).
- The recorded `MeetingDetected.window_title` is still the human-readable window title — Whatever the WASAPI-or-title tier picked.

**`Settings.transcription_live_enabled` (new property, `config.py`).**
- New default in `DEFAULT_SETTINGS["transcription"]`: `"live_enabled": False`.
- Returned by `transcription_live_enabled -> bool` property.

**`Pipeline._start_recorder` (modified, `pipeline.py`).**
- Gates `LiveTranscriber` instantiation on `settings.transcription_live_enabled`. When `False`: no callback wired to the recorder, no live transcriber, no live events fire. The existing Phase 5 wiring runs only when the user opts in.
- New error handling: catches `NoAudioDevicesError` from `RealAudioSource.from_settings`, publishes `RecordingFailed` with a clear human-readable reason, does not partial-create a Recorder.

**`SettingsDialog` (modified, `settings_dialog.py`).**
- New "Audio" tab. Existing audio-adjacent settings move here:
  - Microphone (`QComboBox`) — "Use Windows default" + every enumerated mic.
  - System audio source / loopback (`QComboBox`) — "Use Windows default" + every enumerated speaker (the speaker output we'll loopback-capture).
  - Retention days (existing).
  - Bitrate kbps (existing).
- Dropdowns populate on dialog open via `soundcard.all_microphones(exclude_monitors=True)` and `soundcard.all_speakers()`.
- On save: persists `{id, name}` for the selected device, or `None` for "Use Windows default".
- "Transcription" tab (existing) gains a new checkbox: "Stream transcription during recording (experimental)". Bound to `transcription.live_enabled`.

**`App._on_recording_failed` (modified, `ui/app.py`).**
- Existing handler shows a toast. Phase 6 extends it: when the `RecordingFailed.error_message` mentions "no audio devices" (or matches a sentinel `RECORDING_FAILED_NO_DEVICES`), the toast adds an action button "Open Settings" that opens the Audio tab of the settings dialog.
- New `_on_recording_device_fallback` handler subscribed to `RecordingDeviceFallback` — shows a non-blocking toast: "Saved microphone 'X' not connected — using Windows default. Open Settings → Audio to choose a different device."

**`WorkspaceWindow` (modified, `ui/workspace_window.py`).**
- Already supports both live and past modes (Phase 5). When `live` is True but `settings.transcription_live_enabled` is False (e.g., the recording was started after Phase 6 turned live off), the workspace should still open in "recording" visual mode (red dot in titlebar, stop button visible) but the transcript pane shows a placeholder card: "Transcription will appear when the meeting ends." When `SummaryReady` fires for this recording, the workspace listens (via the existing bridge) and reloads the transcript pane from `TranscriptRepo`.
- New constructor flag is not needed — the workspace can read `settings.transcription_live_enabled` directly at construction time and choose the live-vs-placeholder behavior.

### Dependencies

- New: **`pycaw >= 20231007`** in `[project.dependencies]`. Tiny COM-wrapper package. Used only by `wasapi_sessions.py`. The runtime import is wrapped in a try/except so a broken `pycaw` install at runtime degrades gracefully (returns empty set).

### Data flow — meeting auto-detection (new)

```
MeetingWatcher.step()
   │
   ├── enumerate_windows()   ──► windows: list[WindowInfo]
   │
   ├── audio_session_probe() ──► pids: set[int]   (Teams processes capturing audio)
   │
   ▼
_find_meeting_window(windows, pids):
   if pids:
       candidates = [w for w in windows if w.pid in pids and not _nav_view(w.title)]
       if candidates:
           return _pick_meeting_window(candidates)   # longest non-denylist title
   # fall through to title-pattern matching (Phase 2 logic)
   for w in windows:
       if w.process_name in TEAMS_PROCESS_NAMES and matches_title_pattern(w):
           return w
   return None
```

### Data flow — recording start (new)

```
Pipeline._start_recorder(source_type, detected_title)
   │
   ├── source = RealAudioSource.from_settings(self._settings)
   │     - tries saved mic id → saved mic name → Windows default
   │     - tries saved loopback id → saved loopback name → Windows default
   │     - raises NoAudioDevicesError if everything fails
   │     - publishes RecordingDeviceFallback for any fallback step
   │
   ├── if transcription_live_enabled:
   │       live = LiveTranscriber(...); live.start(rec_id)
   │       audio_chunk_callback wired
   │   else:
   │       live = None
   │       audio_chunk_callback = None
   │
   ├── recorder = Recorder(audio_source=source, audio_chunk_callback=...)
   │
   └── return rec_id
```

### Settings dialog structure (after Phase 6)

```
SettingsDialog
├── General tab          (existing — auto_launch, etc.)
├── Audio tab            (NEW — was scattered across other places)
│   ├── Microphone:          [QComboBox]  "Use Windows default" | ...devices...
│   ├── System audio source: [QComboBox]  "Use Windows default" | ...devices...
│   ├── Retention (days):    [QSpinBox]
│   └── Bitrate (kbps):      [QSpinBox]
├── Detection tab        (existing — title patterns, poll interval)
├── Transcription tab    (existing — model, compute_type)
│   └── [checkbox] Stream transcription during recording (experimental)   ← NEW
├── AI tab               (existing — Claude model, API key, prompt addendum)
└── Shortcuts tab        (Phase 5 — hotkey rebinding)
```

## Error handling

| Failure mode                                  | Behavior                                                                                                  |
|------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| `pycaw` not installed or import fails          | `teams_active_capture_pids` returns empty set. Watcher uses title fallback. Logged at WARNING once.       |
| COM call inside `wasapi_sessions` throws       | Caught + logged. Function returns empty set. Watcher unaffected.                                          |
| Saved mic device not connected                 | Falls back to Windows default. `RecordingDeviceFallback` toast surfaces the swap.                         |
| No audio devices at all                        | `NoAudioDevicesError` → `RecordingFailed(error_message=...)`. Toast with "Open Settings" action. Tray → ERROR. No partial recording row created. |
| Saved device disappears mid-recording          | Recorder loop already handles this (existing behavior — falls through to end-of-stream). Recording finalizes early. |
| `live_enabled=False` but a saved Phase 5 toggle persists in settings.json | Read defaults: `"live_enabled"` will be absent → reads as False (the new default). |
| Live mode disabled, workspace open during recording | Placeholder shown in transcript pane; reloads on `SummaryReady`.                                       |

## Testing

### Unit tests

- **`Settings.audio_mic_device` / `audio_loopback_device`** — dict round-trip, None handling, deep-merge with partial user settings.
- **`Settings.transcription_live_enabled`** — default False, override True via settings.json.
- **`RealAudioSource.from_settings`** — lookup-ladder with stubbed `soundcard`: id-match success, id-miss/name-match, all-miss/default, all-miss-no-default → raise.
- **`RecordingDeviceFallback`** event published when ID lookup fails but name lookup succeeds.
- **`wasapi_sessions.teams_active_capture_pids`** — with `pycaw` mocked: returns set when Teams session active, empty set when not, empty set when COM call throws, empty set when import fails.
- **`MeetingWatcher`** — `audio_session_probe` returning a non-empty pid set short-circuits title matching; returning empty falls through to title matching.

### Integration tests

- **Pipeline e2e with `live_enabled=False`** — recorder starts, no `LiveTranscriber` instantiated, no live events fire, meeting end → batch path runs → summary fires.
- **Pipeline e2e with `live_enabled=True`** — existing Phase 5 behavior unchanged.
- **MeetingWatcher with hybrid detection** — title patterns work when audio probe is empty; audio probe trumps when both signal a meeting.

### UI tests (offscreen Qt)

- **Settings dialog Audio tab** — dropdowns populate from a stubbed device enumeration, round-trip save/load preserves selection, "Use Windows default" round-trips to `None`.
- **Settings dialog Transcription tab** — live_enabled checkbox round-trips.
- **App `_on_recording_failed` toast** — when `error_message` matches the no-devices sentinel, toast shows the "Open Settings" action and it opens the Audio tab.

### Smoke / manual

- Real Surface or other Windows PC: verify the new dropdowns enumerate actual devices, selecting a specific mic uses it (visible in `mmsys.cpl` "this device is in use" indicators).
- Real Teams call: verify WASAPI detection fires correctly when a meeting starts AND title matches don't (e.g., a meeting named "Meeting #2847391" with no recognizable title pattern).

## Open / decided design points

1. **Audio tab placement of retention + bitrate.** Decision: move them. They're audio settings and Phase 5 left the dialog with audio-related fields scattered across General and Transcription. One clean home.
2. **`live_dual_channel` Phase 2.5 leftover setting.** Decision: leave it as-is (still in DEFAULT_SETTINGS, still ignored). Removing it isn't worth a migration; it's harmless dead config.
3. **WASAPI probe poll interval.** Decision: piggybacks on the existing `MeetingWatcher` 2-second poll. No separate timer.
4. **What happens if the user disables live during a recording?** Decision: live persists until that recording ends — the toggle takes effect for the *next* recording. Mid-recording reconfigure is out of scope and creates pipeline state complexity for negligible value.

## Risks

| Risk                                                                   | Mitigation                                                                                              |
|------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| `pycaw` doesn't support capture sessions cleanly                       | Use comtypes directly via a thin internal wrapper if `pycaw.AudioUtilities.GetAllSessions()` proves render-only. Encapsulated in `wasapi_sessions.py`. |
| Saved device dict format breaks existing settings.json files           | New format is fully backwards-compatible: `None` is the default, and an old `str` value loads as `None` (the user re-picks on next dialog open). |
| Windows audio service broken (no devices at all, e.g. current Surface) | The existing `RecordingFailed` toast surfaces a clear message and the Settings deep-link.               |
| Title patterns become "dead" because WASAPI always wins                | They still run as a fallback when WASAPI returns empty. Keeps backwards compatibility for any environment where audio sessions aren't visible (e.g., remote-desktop sessions). |
| pycaw COM init contends with `soundcard`'s WASAPI use                  | `pycaw` only initializes COM on the calling thread. The watcher polls on a single dedicated thread; `soundcard` runs on a separate recorder thread. No contention. |
