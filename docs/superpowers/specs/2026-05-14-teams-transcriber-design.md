# Teams Transcriber — Design

**Status:** Draft for review
**Date:** 2026-05-14
**Author:** Brian Lewis (lewis.briang@gmail.com)

## 1. Purpose

A background Windows application that automatically records and transcribes Microsoft Teams meetings, then uses Claude to produce a structured summary with action items, to-dos, and follow-ups. The app also supports on-demand manual recording (e.g. phone calls, in-person meetings playing through your speakers, training videos). All transcripts and summaries are stored locally and searchable.

This is a personal productivity tool for a single user, running on a single Windows 10 machine with an NVIDIA RTX 3050 Ti Laptop GPU.

## 2. Goals & non-goals

### Goals
- Detect Teams meetings automatically and start recording without user intervention.
- Allow the user to cancel an auto-recording within a short grace window (10 seconds).
- Produce a structured summary per meeting: narrative summary, key decisions, the user's own to-dos, action items for others, follow-ups, and topics.
- Distinguish the user's own speech from remote participants without requiring speaker-diarization models (achieved via separate-channel recording).
- Searchable history of all transcripts and summaries, including full-text search across transcript text.
- Manual recording from a tray icon for non-Teams scenarios.

### Non-goals (deferred to v2 or later)
- Export integrations to external task managers (Todoist, Microsoft To Do, Asana, etc.).
- Multi-user / multi-machine sync.
- Identification of individual remote speakers ("Speaker 1 = Sarah") via diarization.
- Mobile or cross-platform support (Windows only).
- Code-signing of the binary (acceptable Windows SmartScreen warning on first run).
- Calendar integration (e.g. matching recordings to Outlook events).

## 3. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Language / runtime | Python 3.11+ | Mature AI/audio ecosystem |
| UI | PySide6 (Qt 6) | Tray + main window + dialogs |
| Audio capture | `soundcard` (WASAPI loopback + mic); `pyaudiowpatch` fallback | 16 kHz mono, 2-channel Opus output |
| Transcription | `faster-whisper` (CTranslate2) | Model `large-v3-turbo`, `int8_float16` quantization |
| Summarization | Anthropic SDK; default model `claude-sonnet-4-6` | Tool-use / structured-output to enforce JSON schema |
| Storage | SQLite (with FTS5) + files on disk | App data under `%LOCALAPPDATA%\TeamsTranscriber\` |
| Teams detection | `pywin32` (`EnumWindows`) + `psutil` | 2-second polling, debounced state machine |
| Toast notifications | `winsdk` (preferred) or `win10toast-click` fallback | Windows native toasts |
| Global hotkeys | `keyboard` library | Configurable bindings |
| Secrets | `keyring` (Windows Credential Manager) | Claude API key, not stored in plaintext |
| Packaging | PyInstaller (one-folder) + Inno Setup | `.exe` installer, install to `%LOCALAPPDATA%\Programs\` |

## 4. System architecture

A single Python process. Components communicate via Qt signals/slots (in-process event bus). Each pipeline component runs on its own `QThread` to keep the UI responsive.

```
┌─────────────────────────────────────────────────────────────────┐
│                    Teams Transcriber (PySide6 app)              │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐   │
│  │ Tray + Win   │◄──►│  EventBus    │◄──►│  MeetingWatcher  │   │
│  │ (UI layer)   │    │ (Qt signals) │    │  (Teams poller)  │   │
│  └──────────────┘    └──────┬───────┘    └──────────────────┘   │
│         ▲                   │                                   │
│         │            ┌──────┴──────┐                            │
│         │     ┌──────▼─────┐ ┌─────▼──────┐  ┌───────────────┐  │
│         └─────┤  Recorder  │ │Transcriber │  │  Summarizer   │  │
│               │ (WASAPI +  │ │ (faster-   │  │  (Claude API) │  │
│               │  mic, 2ch) │ │  whisper)  │  │               │  │
│               └──────┬─────┘ └─────┬──────┘  └───────┬───────┘  │
│                      │             │                 │          │
│                      └─────────┬───┴─────────────────┘          │
│                                ▼                                │
│                       ┌─────────────────┐                       │
│                       │  Storage layer  │                       │
│                       │ SQLite + files  │                       │
│                       └─────────────────┘                       │
└─────────────────────────────────────────────────────────────────┘
```

**Components:**

- **MeetingWatcher** — polls Teams windows every 2 s; emits `meeting_started` / `meeting_ended`.
- **Recorder** — captures WASAPI loopback + mic on two channels; writes streaming Opus.
- **Transcriber** — runs `faster-whisper` per channel; emits transcript segments live as they're produced.
- **Summarizer** — sends finished transcript to Claude; receives structured JSON output.
- **Storage** — SQLite for metadata/transcripts/summaries (with FTS5); audio files on disk; retention pruning.
- **Tray + Window (UI)** — tray icon with state, context menu, toasts, main window, settings dialog.

Each component is independently testable. The Recorder is unaware of how transcription works; the Transcriber is unaware of which LLM summarizes. Swapping the LLM provider or transcription engine is a single-file change.

## 5. Recording & transcription pipeline

### 5.1 Meeting detection (MeetingWatcher)

- Poll every 2 s using `pywin32`'s `EnumWindows`.
- For each top-level window, check process name = `ms-teams.exe` and apply title-pattern matching (substring or regex; configurable) against a list of starting patterns:
  - `Meeting in progress | Microsoft Teams`
  - `Meeting | Microsoft Teams`
  - `| Microsoft Teams Call` (substring — matches any call name ending in this suffix)
- Patterns live in `config\settings.json` so the user can extend or replace them without code changes. We also support a `meeting_title_regex` pattern for advanced users.

State machine:

```
IDLE → CANDIDATE (1 poll matches) → IN_MEETING (2 consecutive polls match)
IN_MEETING → LEAVING (window gone for 1 poll) → IDLE (window gone for 2 polls)
```

Debouncing avoids flapping on transient title changes. Every window title observed is logged at DEBUG level so the user can find new patterns from the log if Microsoft changes things.

Emits:
- `meeting_started(window_title: str, started_at: datetime)`
- `meeting_ended(ended_at: datetime)`

### 5.2 Audio capture (Recorder)

- **Library:** `soundcard` (primary), `pyaudiowpatch` (fallback).
- **Sources:** Default render device's loopback (system audio) + default capture device (mic). Both configurable in settings.
- **Format:** 16 kHz, 16-bit, mono per channel; written as 2-channel Opus in an Ogg container. Channel 0 = mic ("me"), channel 1 = loopback ("others"). Bitrate default 24 kbps per channel (~20 MB/hr total).
- **Write strategy:** Audio chunks of ~5 s are flushed to disk. A crash mid-recording leaves a valid, transcribable Opus file containing everything up to the last flush.
- **Toast + cancel:** On `meeting_started`, a Windows toast appears: "Recording '<title>'. Click to cancel." Recording starts immediately into a temp path. If the user clicks Cancel within 10 s, the file is deleted and the in-progress recording row is removed from the DB. We prefer record-then-delete over wait-then-record to avoid missing the first 10 seconds.

### 5.3 Transcription (Transcriber)

- **Engine:** `faster-whisper` with model `large-v3-turbo`, `int8_float16` quantization on GPU.
- **Mode:** Live, per-channel. Each channel feeds its own Whisper instance using VAD-based chunking. Segments are persisted to SQLite incrementally as they're produced (channel label, start/end ms, text).
- **VRAM fallback:** If VRAM is insufficient for two simultaneous Whisper instances, fall back to a single instance processing channels sequentially in batches. Transcripts are no longer live but everything else is unchanged. This fallback is detected by trapping `RuntimeError: CUDA out of memory` and is exposed as a settings option (`transcription.live_dual_channel`).
- **Output schema:** list of `{recording_id, start_ms, end_ms, channel: "me" | "others", text}` persisted in `transcript_segments`.

### 5.4 Stop & finalize

On `meeting_ended`:
1. Show a 10-second toast: "Still in meeting? Click to extend by 60s."
2. If not extended: flush audio file, wait for live transcription to drain (or run post-transcription if in fallback mode), then enqueue summarization.
3. If extended: keep recording for 60 s more, then re-show the extend toast. Repeated extension is just repeated clicks. If the meeting window reappears during an extension, return to the normal `IN_MEETING` state and the next `meeting_ended` will trigger the same flow.

### 5.5 Manual recording

Uses the same Recorder. No meeting title is detected — the recording is labeled "Manual recording — \<timestamp\>" until the AI summary assigns a `display_title`. Same channel separation (mic = "me", system = "others"); useful when listening to a phone call on speakerphone or a video the user wants notes from.

## 6. AI processing

### 6.1 Trigger and retries

`Summarizer` listens for `transcription_complete(recording_id)`. Runs once per recording. On failure (network, API, JSON parse error), retries with exponential backoff: 1 s, 5 s, 30 s. After three failures, records `status='summary_failed'` and `error_message` on the recording row. The user can right-click in history → "Retry summary."

### 6.2 Output schema

Claude is called with tool-use to enforce a structured JSON response:

```json
{
  "title": "Q2 roadmap sync",
  "one_line": "Aligned on shipping the billing rewrite by July; Sarah owns API stub.",
  "summary": "3-5 sentence narrative summary of what was discussed and decided.",
  "key_decisions": [
    "Billing rewrite scheduled for July release",
    "Drop the legacy webhook path"
  ],
  "my_todos": [
    {"task": "Write API stub spec by Friday", "context": "Discussed at ~12:30", "due": "2026-05-16"}
  ],
  "action_items_others": [
    {"who": "Sarah", "task": "Review billing migration doc", "due": null}
  ],
  "follow_ups": [
    "Revisit pricing tiers after legal review",
    "Open question: how do we handle existing annual contracts?"
  ],
  "topics": ["billing", "roadmap", "Q2 planning"]
}
```

The "me" vs "others" channel labels in the transcript inform the model which actions belong in `my_todos` vs `action_items_others`.

### 6.3 Model and cost

- **Default model:** `claude-sonnet-4-6`. Estimated cost ~\$0.02 per 30-minute meeting, ~\$0.04 per hour.
- **Configurable** in settings: `claude-opus-4-7` (higher quality) or `claude-haiku-4-5` (cheaper batch reprocessing).
- **Prompt caching** is used on the system prompt + JSON schema so repeated calls in a session are cheaper.

### 6.4 Long meetings

If the transcript exceeds ~150k input tokens (rare; ~5 hours of dense speech), split into ~60-minute chunks: summarize each chunk, then summarize-of-summaries. This branch is implemented but rarely exercised in practice.

### 6.5 User customization

`config\prompts.json` contains the editable summary prompt and JSON schema. A `custom_prompt_addendum` setting (e.g. "I work on the billing team — focus on billing-relevant decisions") is appended to the system prompt without giving the user a way to break the JSON schema.

## 7. UI

### 7.1 Tray icon

States:
- **Idle** (gray) — running, not recording, not processing.
- **Recording** (red, pulsing) — active recording.
- **Processing** (yellow) — transcribing or summarizing.
- **Error** (orange with `!`) — last operation failed.

Left-click opens main window.

Right-click menu:

```
● Recording: <meeting name> (12:34)       [shown only when recording]
  Stop recording
  ──────────────
  Start manual recording
  Stop manual recording                    [shown only when manually recording]
  ──────────────
  Open Teams Transcriber
  Recent meetings ►
     ├ Q2 roadmap sync — 2h ago
     ├ 1:1 with Mike — yesterday
     └ ... 5 most recent
  ──────────────
  Settings
  Pause auto-detection                     [toggle]
  ──────────────
  Quit
```

### 7.2 Toasts

- **Meeting detected:** "Recording '<title>'. Click to cancel." (10 s, dismissible)
- **Meeting ending:** "Still in meeting? Click to extend by 60s." (10 s)
- **Summary ready:** "Summary ready for '<title>'." Click opens detail view.
- **Error:** "Summary failed for '<title>'. Click for details."

### 7.3 Main window

Two-pane layout: history list on the left, detail view on the right.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Teams Transcriber                                          ─ □ ✕     │
├──────────────────────────────────────────────────────────────────────┤
│ [+ Manual record]  [⚙ Settings]    [🔍 Search...]                    │
├─────────────────┬────────────────────────────────────────────────────┤
│ HISTORY         │ DETAIL VIEW                                        │
│                 │                                                    │
│ Today           │ Q2 roadmap sync                         ●●● menu   │
│ ● Q2 roadmap …  │ 2026-05-14, 11:00 AM · 47 min · Teams              │
│   11:00 · 47m   │                                                    │
│                 │ ┌─ Summary ─────────────────────────────────────┐  │
│ Yesterday       │ │ Aligned on shipping the billing rewrite by    │  │
│ ● 1:1 with Mike │ │ July; Sarah owns the API stub spec, due …     │  │
│   2:30 · 22m    │ └───────────────────────────────────────────────┘  │
│                 │                                                    │
│ This week       │ My todos                                           │
│ ● Sprint plan…  │  ☐ Write API stub spec by Friday (due 2026-05-16) │
│                 │                                                    │
│ Earlier         │ Action items for others                            │
│ ● ...           │  · Sarah — Review billing migration doc            │
│                 │                                                    │
│                 │ Key decisions                                      │
│                 │  · Billing rewrite scheduled for July release      │
│                 │                                                    │
│                 │ Follow-ups                                         │
│                 │  · Revisit pricing tiers after legal review        │
│                 │                                                    │
│                 │ [▶ Play audio]  [View transcript]  [Copy markdown] │
└─────────────────┴────────────────────────────────────────────────────┘
```

- **History list:** grouped by date bucket (Today / Yesterday / This week / Earlier). Items show display title, time, duration.
- **Detail pane:** structured summary; transcript is one click away as a separate scrollable view with channel-labeled, timestamp-prefixed segments. Clicking a transcript segment seeks the audio (if retained).
- **Right-click on history item:** Re-summarize · Re-transcribe (only if audio still present) · Rename · Delete · Export as markdown.

### 7.4 Global search

A search bar in the toolbar. Backed by SQLite FTS5 on `transcript_segments.text` (and also indexes the `summaries` fields). Results list shows: title, date, and a highlighted snippet from the matching transcript or summary. Clicking a result opens the detail view, scrolled to the matching transcript segment.

### 7.5 Settings dialog

Sections:
- **General:** Auto-launch on Windows (default on); pause-auto-detection-on-startup (default off — when on, the app starts in "paused" state and won't auto-record until you toggle from the tray).
- **Audio:** Mic device, loopback device, audio retention days (default 30, configurable), bitrate.
- **Detection:** Title patterns, poll interval, debounce counts.
- **Transcription:** Model, compute type, language hint, `live_dual_channel` toggle.
- **AI:** Claude API key (stored via `keyring`), model, custom prompt addendum.
- **Hotkeys:** Bindings for `toggle_manual_recording` and `toggle_pause_detection`.

### 7.6 Global hotkeys

- `Ctrl+Alt+R` — toggle manual recording.
- `Ctrl+Alt+P` — toggle pause auto-detection.
- Customizable in settings.

## 8. Storage & file layout

### 8.1 File layout

```
%LOCALAPPDATA%\TeamsTranscriber\
├── teams_transcriber.db           # SQLite — metadata, transcripts, summaries, FTS
├── audio\
│   ├── 2026-05-14_110000_q2-roadmap-sync.opus
│   └── ...                        # auto-pruned by retention policy
├── models\
│   └── faster-whisper-large-v3-turbo\
├── logs\
│   └── app.log                    # rotating, 10 MB × 5 files
└── config\
    ├── settings.json
    └── prompts.json
```

API key: **Windows Credential Manager** via `keyring`, never written to disk in plaintext.

### 8.2 Database schema

```sql
recordings (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,          -- ISO 8601 UTC
  ended_at TEXT,
  source TEXT NOT NULL,              -- 'teams' | 'manual'
  detected_title TEXT,               -- raw window title or NULL for manual
  display_title TEXT,                -- AI-assigned, user-editable
  audio_path TEXT,                   -- NULL once retention deletes it
  audio_deleted_at TEXT,
  duration_ms INTEGER,
  status TEXT NOT NULL,              -- recording | transcribing | summarizing | done
                                     -- | recording_failed | transcription_failed | summary_failed
  error_message TEXT
);

transcript_segments (
  id INTEGER PRIMARY KEY,
  recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
  start_ms INTEGER NOT NULL,
  end_ms INTEGER NOT NULL,
  channel TEXT NOT NULL,             -- 'me' | 'others'
  text TEXT NOT NULL
);

-- Contentless FTS index over transcript_segments.text and summaries fields, kept in sync via triggers.
CREATE VIRTUAL TABLE transcript_fts USING fts5(text, content='transcript_segments', content_rowid='id');

summaries (
  recording_id INTEGER PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
  one_line TEXT,
  summary TEXT,
  key_decisions_json TEXT,           -- JSON array
  my_todos_json TEXT,
  action_items_others_json TEXT,
  follow_ups_json TEXT,
  topics_json TEXT,
  generated_at TEXT NOT NULL,
  model_used TEXT NOT NULL
);

todo_state (                         -- "checked off" state for my_todos, survives re-summarization
  id INTEGER PRIMARY KEY,
  recording_id INTEGER NOT NULL,
  todo_index INTEGER NOT NULL,
  task_text TEXT NOT NULL,
  done BOOLEAN NOT NULL DEFAULT 0,
  done_at TEXT
);
```

Schema migrations versioned via `PRAGMA user_version`. Bumped per change with a migration script.

### 8.3 Retention

A background job (runs at app startup and once per 24 hours) deletes audio files older than `audio.retention_days` (default 30) and sets `audio_path = NULL`, `audio_deleted_at = now()` on the corresponding row. Transcripts and summaries are never auto-deleted (the user can delete individual recordings manually).

### 8.4 Auto-launch

Write `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\TeamsTranscriber` pointing at the installed `.exe`. No admin elevation required. Toggleable from settings.

## 9. Packaging & distribution

- **PyInstaller one-folder build:** faster startup than one-file; output `dist\TeamsTranscriber\TeamsTranscriber.exe` + bundled DLLs.
- **Inno Setup installer:** drops the app under `%LOCALAPPDATA%\Programs\TeamsTranscriber\`, creates Start Menu shortcuts, optionally enables auto-launch on install.
- **Whisper model:** not shipped in the installer; downloaded on first app start (~1.5 GB) with a progress indicator. Lives in `%LOCALAPPDATA%\TeamsTranscriber\models\`.
- **Code signing:** skipped for v1. Windows SmartScreen will warn on first run; the user clicks "More info → Run anyway."

## 10. Testing approach

### 10.1 Unit tests (fast, deterministic)

- `MeetingWatcher` state machine — mock window-enumeration; cover debouncing, start/stop transitions, rapid join/leave, title flicker.
- `Summarizer` — mock Anthropic SDK; canned transcripts; assert JSON shape, retry logic, error paths.
- `Storage` — real in-memory SQLite (`:memory:`); test schema, FTS triggers, retention pruning, migrations.
- Settings load/save, retention policy, hotkey parsing.

### 10.2 Integration tests (slower, automated)

- Pre-recorded 30-second `.opus` fixtures (2-3 short clips: two-speaker, silence/noise, technical jargon) run through the real Transcriber and a mocked Summarizer. Assert DB end state.
- Real `faster-whisper` (small model) downloaded once into the test env.

### 10.3 Manual verification

- Audio capture against real Windows devices (must listen).
- Tray icon visuals, toast notifications.
- Teams window detection against real meeting types (scheduled, ad-hoc, call). Kept as a written checklist.
- Global hotkeys.

### 10.4 TDD

Where unit tests apply, follow TDD (test-first) per the test-driven-development skill — state machine, storage layer, summarizer logic. For audio/UI surfaces, TDD doesn't fit cleanly; rely on integration tests against fixtures or manual verification.

## 11. Implementation phasing

The detailed plan comes from the writing-plans skill; rough slice order:

1. Storage layer + schema + migrations.
2. MeetingWatcher (no audio).
3. Recorder (capture, channel separation, file output).
4. Transcriber (post-meeting first; live as a follow-up).
5. Summarizer (Claude with structured output).
6. Tray + toasts (minimal UI — app already usable).
7. Main window — history list + detail view.
8. Search (FTS).
9. Settings dialog + global hotkeys.
10. Packaging (PyInstaller + Inno Setup).

Each slice produces something usable. After slice 6 the app does its core job; 7-10 are polish.

## 12. Known risks & limitations

1. **VRAM pressure for live dual-channel Whisper** — mitigation: fall back to sequential per-channel transcription. Toggleable.
2. **Teams title-pattern drift** — Microsoft renames things. Mitigation: patterns are editable; all observed window titles are logged.
3. **Toast-cancel race** — user may click Cancel after several seconds of audio is on disk. Mitigation: delete the file rather than try to "un-record."
4. **First-run model download** — `faster-whisper` model is ~1.5 GB. Mitigation: download at first app launch with visible progress; do not block first meeting on it.
5. **Overlapping meetings** — v1 records whichever meeting started first; subsequent overlapping meetings are logged but not recorded. Documented as a known limitation.
6. **Legal/consent** — recording meetings without participant notification may be problematic depending on jurisdiction and workplace policy. This is the user's responsibility; the app provides "pause auto-detection" as a one-click way to opt out for sensitive meetings.

## 13. Open questions for v2

- Export integration with task managers (Microsoft To Do is the most likely target given the user's Microsoft 365 use).
- Speaker diarization for individual remote speakers via `pyannote.audio`.
- Outlook calendar correlation (match recordings to scheduled events).
- Code-signing the binary so SmartScreen doesn't warn.
