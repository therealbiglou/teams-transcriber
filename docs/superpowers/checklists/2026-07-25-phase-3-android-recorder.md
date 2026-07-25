# Phase 3 — Android recorder on-device checklist

Manual verification of the framework code (recording, foreground service,
notification, storage, Teams auto-record) on Brian's Pixel, plus a full USB
round-trip through the already-built desktop sync (Phases 1–2). The pure
logic (sync contract, guards, elapsed/uid/timestamp, detector predicate) is
already covered by JUnit (`gradlew testDebugUnitTest`); this checklist covers
what only a device can prove.

## Install

From `android/`, with the Pixel connected and USB debugging on, proxy scrubbed:

```powershell
$env:HTTPS_PROXY=$null; $env:HTTP_PROXY=$null; $env:https_proxy=$null; $env:http_proxy=$null
& "C:\Dev\teams-transcriber\android\gradlew.bat" -p "C:\Dev\teams-transcriber\android" installDebug --console=plain
```

Grant on first run:
- App → **"Grant mic / notifications"** → allow both.
- App → **"Grant file access"** → toggle **All files access** ON in Settings.
- Android Settings → Apps → Special app access → **Notification access** →
  enable **TT Companion** (required for Teams auto-record).

## Checks (each with its expected result)

1. **Manual record — voice memo.** Tap **Record → Voice memo**. Expect a
   persistent notification "Recording — Voice memo" with a ticking timer and a
   **Stop** action.
2. **Stop from the notification.** Notification clears. Expect
   `Documents/TeamsTranscriber/outbox/rec_<uid>.m4a` + `rec_<uid>.json` on the
   phone. Sidecar: `source` = `memo`; `started_at`/`ended_at` end in `+00:00`
   (not `Z`); `duration_ms` ≈ the recording length; `app_version` set.
3. **Manual record — in-person.** Repeat check 1–2 via **In-person meeting**;
   sidecar `source` = `in_person`.
4. **Screen-off survival.** Start a recording, turn the screen off ~30 s, stop.
   Expect a complete, playable `.m4a` (no truncation) and a valid pair.
5. **Auto-record a Teams call.** Enable the **Auto-record Teams calls** toggle,
   place/receive a Teams call on the phone. Expect recording to start
   automatically with source `teams_call` and stop when the call ends. If it
   does NOT trigger, capture the Teams call notification's `category` and
   ongoing flag (via `adb shell dumpsys notification`) and tune
   `TeamsCallDetector` — the heuristic is expected to need on-device tuning.
6. **Failure is never silent.** Revoke All-files access, then record + stop.
   Expect a **"Recording not saved"** / **"Not enough free storage"**-style
   notification, not a silent drop. (Also: a <1 s tap-record-then-stop should
   surface "Recording too short — nothing saved".)
7. **Desktop USB round-trip.** With recordings in the outbox, connect the phone
   to the desktop with phone-sync enabled. Expect the desktop to import them as
   source-tagged meetings, transcribe + summarize, and clear the outbox — the
   full Phase 1–2 sync, now fed by the phone.

## Results

_Record pass/fail per item and any `TeamsCallDetector` tuning here as the
checklist is run; feed findings back into the phase notes._
