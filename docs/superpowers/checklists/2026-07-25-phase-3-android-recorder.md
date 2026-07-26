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

## Results — run 1 (2026-07-25, Pixel 8 Pro, Android 17, adb-driven)

- **1–2 Manual record (voice memo): PASS.** Recorded via adb taps. `RecordingService`
  confirmed foreground, type `0x80` (microphone), ongoing Stop notification. On stop:
  outbox held exactly `rec_4b3d4110b1b2.m4a` (229025 B ≈ 64 kbps for 28.6 s) +
  `rec_4b3d4110b1b2.json`, no `.tmp` left. Sidecar was contract-perfect:
  `{"uid":"4b3d4110b1b2","title":"Voice memo — Jul 25, 1:16 PM","source":"memo",
  "started_at":"2026-07-25T18:16:45+00:00","ended_at":"...+00:00","duration_ms":28632,
  "app_version":"0.1.0"}` — all 7 keys, `memo` wire value, `+00:00` (no `Z`), real audio bytes.
- **UI:** Compose renders correctly (Record → In-person/Voice memo/Cancel picker → active
  Recording screen → Stop). Grant buttons + toggle present. Notification-listener binds
  ("service connected") on launch.
- **In-app timer:** confirmed FROZEN at 0:00 (known Minor; notification carries the live timer).
- **4 Teams auto-record: DETECTOR WORKS, but the start CRASHES (blocker).**
  `TeamsCallDetector` correctly matched a real Teams call (`com.microsoft.teams`) — proven by
  the crash reaching `RecordingService.begin()`. But:
  `java.lang.SecurityException: Starting FGS with type microphone ... requires ... the app must
  be in the eligible state/exemptions` at `startForeground` (RecordingService.kt:63), from
  `onStartCommand` (line 44). **Android 14+ forbids starting a microphone foreground service
  from the background**, which is where the `NotificationListenerService` callback runs. The
  unguarded `startForeground` then crashes the app ("TT Companion keeps stopping"), and the
  ticking call notification re-triggers it → crash loop while armed.
- Not yet run: 3 (in-person), 5 real Teams end-to-end, 6 failure-not-silent, 7 desktop USB round-trip.

### Two fixes this blocker requires (see progress.md)
1. **Crash guard (unconditional):** wrap `startForeground`/`begin()` in try/catch → `notifyError`
   + graceful stop (the reviewer's flagged Minor, now proven Critical on-device). Prevents the
   crash but leaves auto-record non-functional.
2. **Auto-record architecture (design decision needed):** the spec's assumption that
   `TeamsCallWatcher` can start mic recording directly does not hold on Android 14+. Options to
   present to Brian: (A) persistent mic FGS armed-from-foreground while auto-record is enabled
   (verify whether the mic indicator only shows on actual `AudioRecord` access, not while merely
   armed); (B) foreground-only best-effort auto-record; (C) a "tap to record this Teams call"
   high-priority notification that foregrounds the app then records. Manual record is unaffected
   and fully working.

## Run 2 — pending (reinstall build 57b30b0, armed-FGS auto-record)

Auto-record was rearchitected to the "persistent armed recorder" (Brian's choice): the app arms a
microphone FGS from the foreground and the background listener signals it in-process, so no
background FGS start occurs. Reinstall and verify:

- Reinstall: `adb install -r android/app/build/outputs/apk/debug/app-debug.apk`; re-grant perms if needed.
- **Arm:** toggle **Auto-record Teams calls** ON in the app. Expect a persistent
  **"Watching for Teams calls"** notification. Expect **no** mic in-use indicator while merely armed
  (only when actually recording) — CONFIRM this assumption on-device.
- **Auto-capture:** place a Teams call. Expect the armed notification to swap to "Recording — Teams
  call" and capture to start (NO crash), stopping when the call ends; outbox pair `source":"teams_call"`.
- **No crash on the old failure path:** confirm the app no longer crashes (the earlier
  background-mic-FGS `SecurityException` is gone because capture now starts from the armed foreground service).
- **Re-arm after process death:** with auto-record on, force-stop the app then reopen it —
  `MainActivity.onStart` should re-arm (persistent notification returns) without crashing.
- **Disarm:** toggle OFF → the "Watching…" notification clears and the service stops.
- **Manual while armed:** with auto-record on, do a manual voice-memo record → it should capture and
  save a normal pair, then the notification reverts to "Watching for Teams calls" (service stays armed).
- Still pending from run 1: item 3 (in-person), item 6 (failure-not-silent), item 7 (desktop USB round-trip).
- ~~Open product question (minor #3)~~ **RESOLVED 2026-07-25 (commit `3529d2d`):** disarm no longer
  cuts short a manual recording. Spot-check when convenient: start a voice memo, toggle auto-record
  OFF mid-recording → recording continues (notification still "Recording — Voice memo"); press Stop
  → the pair saves and the service stops. Toggling OFF during an auto-started Teams capture still
  ends and saves that capture.

## Run 2 results (2026-07-25, build 57b30b0) — auto-record PASS

- **Arm:** app launch re-armed from the foreground → mic FGS (type `0x80`), persistent
  "Watching for Teams calls" notification, no crash. ✅
- **Auto-capture a real Teams meeting:** notification swapped to Recording, captured, auto-stopped
  on meeting end, wrote `rec_16fe38fbd001.m4a` (390390 B ≈ 64 kbps / 49 s) +
  `{"source":"teams_call","started_at":"2026-07-25T21:25:49+00:00",…}`. **No SecurityException —
  the run-1 blocker is resolved.** ✅
- **Teams notification shape confirmed:** `pkg=com.microsoft.teams`,
  `channel=com.microsoft.teams.CallsOngoing`, `category=call`, `flags=ONGOING_EVENT` — the detector
  matched with no tuning needed. ✅
- **Manual while armed:** voice memo saved `rec_175dfd37cc9b` (`source":"memo"`) and the service
  stayed foreground/armed. ✅
- **Disarm:** toggle OFF → service stopped, "Watching" notification cleared, pref persisted false. ✅
- Test recordings cleared from the phone afterward.
- Still not run (non-blocking): item 3 (in-person — same code path as memo), item 6
  (failure-not-silent), item 7 (desktop USB round-trip).
