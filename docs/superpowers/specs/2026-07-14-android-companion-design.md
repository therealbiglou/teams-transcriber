# Android Companion — Design

**Date:** 2026-07-14
**Status:** Approved by Brian (brainstorm 2026-07-13/14)

## Purpose

A native Android app that records meetings on the phone — automatically for
Teams calls taken on the phone, manually for in-person meetings and voice
memos — and mirrors the full desktop library (summaries, transcripts, todos,
chat history) for browsing on the go. The desktop app remains the only place
audio is transcribed (GPU faster-whisper) and summarized (Claude); the phone
records, displays, and toggles todos.

**Transport decision:** USB. The phone never pushes; when it appears over
MTP, the desktop pulls new recordings, applies phone-side todo toggles, and
pushes a fresh library export back. Fully local, no cloud, no network
configuration.

## Constraints and accepted trade-offs

- **Mic-only capture on Android.** Apps cannot tap another app's call audio.
  A Teams call on the phone records the user's voice always, and the other
  side only on speakerphone. Accepted. Phone recordings are single-channel;
  the desktop importer already handles channel-less imports.
- **Desktop must run for processing.** Recordings queue in the phone outbox
  until the next USB connection. Accepted.
- **Teams call detection is heuristic** (notification-based). The persistent
  recording notification is the guardrail against mis-triggers.

## Architecture

One repository, two apps:

- `src/teams_transcriber/` — existing desktop app, gains a `phone_sync`
  package.
- `android/` — new Kotlin + Jetpack Compose app (min SDK 29 / Android 10+).

They share no code; they share the **sync contract**, a folder layout on the
phone's shared storage, versioned in lockstep by living in one repo.

### Sync contract

`Documents/TeamsTranscriber/` on the phone:

```
outbox/                       # phone → desktop
  rec_<uuid>.m4a              # finished recording (AAC mono, voice bitrate ~64kbps)
  rec_<uuid>.json             # sidecar: {uid, title, source, started_at, ended_at,
                              #           duration_ms, app_version}
                              # source ∈ teams_call | in_person | memo
  changes.json                # [{recording_id, todo_index, done, toggled_at}]
library/                      # desktop → phone, regenerated every sync
  manifest.json               # {schema_version, exported_at, desktop_version}
  meetings.json               # [{id, title, started_at, duration_ms, status,
                              #   one_line, source, todo_count, todos_done}]
  meetings/<id>.json          # {summary, key_decisions, my_todos (+done state),
                              #  action_items_others, follow_ups,
                              #  transcript: [{start_ms, channel, text}],
                              #  chat: [{role, content, created_at}]}
sync/
  desktop_ack.json            # {imported: [{uid, recording_id, result}],
                              #  changes_applied_through: <timestamp>}
```

Rules:

- **Idempotency:** the desktop keeps a UID ledger (new table mapping phone
  `uid` → `recording_id`); re-pulling an already-imported file is a no-op.
- **Conservative deletion:** an outbox file is deleted from the phone only
  after its import is committed to the desktop DB.
- **Todo conflicts:** last-write-wins by timestamp — phone `toggled_at` vs
  desktop `done_at`. Applied toggles fire the existing Wrike close-loop.
- **Toggle cleanup:** the phone prunes `changes.json` entries with
  `toggled_at ≤ changes_applied_through` from the ack; the desktop never
  edits `changes.json` (single-writer per file, both directions).
- **Forward compatibility:** the app compares `manifest.schema_version` to
  what it understands and shows "update the app" instead of misrendering.

### Sync cycle (desktop-driven)

1. Device watcher notices the phone over MTP (poll, only while enabled).
2. Pull new `outbox/` recordings → `pipeline.import_audio_file` (existing
   transcribe + summarize flow); sidecar supplies title/started_at/source.
3. Apply `changes.json` via `TodoStateRepo.mark_done` → Wrike close-loop.
4. Regenerate `library/` export from the DB.
5. Write `sync/desktop_ack.json`; delete committed outbox items.
6. Toast progress ("Imported 2 recordings from phone").

Phone-sourced recordings appear in the desktop UI like any other meeting
(tagged by source) and are included in the next library export.

## Components

### Desktop — `phone_sync` package

| Unit | Responsibility |
|---|---|
| `transport.py` | Interface: `list/pull/push/delete`. Implementations: `LocalDirTransport` (plain folder — tests, and any folder-sync tool), `MtpTransport` (pywin32 Shell COM). Wi-Fi later = third implementation, no redesign. |
| `sync.py` | The pull→import→toggle→export→ack cycle. Pure logic against the transport interface. |
| `library_export.py` | Pure DB → JSON export builder. |
| Device watcher + settings | Background MTP-arrival poll; "Phone sync" section in Settings → Integrations (enable toggle, status, last-sync). |

### Android app

| Unit | Responsibility |
|---|---|
| `RecordingService` | Foreground service, `MediaRecorder` mic → `.m4a`, persistent notification (elapsed + Stop), survives screen-off, writes file + sidecar to outbox on stop. |
| `TeamsCallWatcher` | `NotificationListenerService`; Teams ongoing-call notification appears → start, disappears → stop. Gated behind an in-app auto-record toggle. |
| Library UI | Meetings list (search, date grouping) + detail screen (summary / decisions / action items / follow-ups / transcript / chat tabs); todo checkboxes append to `changes.json`. Renders directly from `library/` JSON — no phone-side database. |
| Recorder UI | One-tap record with source picker (in-person / memo) + elapsed timer. |

Permissions: `RECORD_AUDIO`, `POST_NOTIFICATIONS`, foreground-service-mic,
notification access (special grant, for TeamsCallWatcher only).

## Error handling

- Recording failure → immediate phone notification, never silent.
- Auto-record guards: max-duration cap, free-storage check.
- Desktop MTP failure → toast + retry on next poll; the UID ledger +
  delete-after-commit rule make any mid-sync cable pull safe (no loss, no
  duplicates).
- Schema mismatch → phone shows update prompt (see contract rules).

## Testing

- **Desktop:** full pytest coverage of the sync cycle via `LocalDirTransport`
  round-trips — import, both toggle-conflict directions, ack/cleanup,
  idempotent re-pull. MTP wrapper is thin and spike-verified manually.
- **Android:** JUnit for contract parse/write and toggle merge; manual
  on-device checklist for recording, Teams auto-record, and a full USB
  round-trip.
- **De-risk first:** an MTP spike against Brian's actual phone (list, pull,
  push, delete via Shell COM) before building on it.

## Phasing (each an independently shippable sub-project with its own plan)

1. **Desktop sync engine** — contract, `LocalDirTransport`, import/toggle/
   export/ack logic, UID ledger migration, tests. Immediately usable with
   any folder-sync tool.
2. **Desktop USB experience** — `MtpTransport` spike + implementation,
   device watcher, settings UI.
3. **Android recorder** — manual + Teams auto-record, outbox writing.
4. **Android library** — full-mirror UI + todo toggles.

## Out of scope (this design)

Wi-Fi/cloud sync (future transport implementation), on-phone transcription
or summarization, recording non-Teams phone calls, iOS.
