# MTP Spike Findings (Android Companion Phase 2)

**Date:** 2026-07-24 · **Device:** Brian's Pixel (VID_18D1/PID_4EE1, "MTP USB
Device") · **Method:** Shell.Application COM from PowerShell (identical
surface to pywin32's `win32com.client.Dispatch("Shell.Application")`).

Everything Phase 2's `MtpTransport` and device watcher must honor:

## Access path

`Shell.Application` → `NameSpace(17)` (This PC) → the item with
`IsFileSystem == False` is the device → `.GetFolder.Items()` are storages
("Internal shared storage") → traverse by repeated
`folder.ParseName(name).GetFolder`. All enumeration is sub-second.

## Operations (all verified against the real phone)

| Operation | Mechanism | Measured |
|---|---|---|
| List + sizes | `folder.Items()`; `item.ExtendedProperty('System.Size')` | instant |
| Push | `deviceFolder.CopyHere(localPath)` — **async**; poll `ParseName` until the item appears | ~200-330 ms (small files) |
| Pull | `localShellFolder.CopyHere(deviceItem)` — async; poll the local path | ~200 ms, byte-accurate |
| Delete (silent) | `localDiscardShellFolder.MoveHere(deviceItem)` — **no confirmation UI**, near-instant; the file lands locally (free audit copy) | ~3 ms to initiate |
| Create folder | `parent.CopyHere(localFolderPath)` (copies the tree); nested empty dirs copied as part of a tree | ~250 ms |

## Hazards (each shaped the transport design)

1. **Overwrite silently no-ops.** `CopyHere` onto an existing name neither
   overwrites nor errors (FOF flags ignored for MTP). Every push must be
   delete-then-push. Library re-exports hit this on every cycle.
2. **Session-cache invisibility.** A successful write can be *invisible* to
   the MTP session that made it (Windows-side object cache), while still
   blocking same-name operations. Observed: `outbox` created successfully,
   unlistable all session, visible immediately after replug. Therefore:
   verify-after-write; on persistent invisibility, fail with a "unplug and
   replug the phone" error state instead of retrying forever.
3. **Staged arrival.** After (re)connect the device may enumerate with zero
   storages: phone locked, or Android reset USB mode to charging (it does
   this per-plug unless Developer Options → Default USB configuration is
   set to File transfer). The watcher treats device-without-storage as
   "waiting" and surfaces unlock/mode guidance, polling patiently (observed
   up to ~60 s before storage appears).
4. **WPDBusEnum service.** The Portable Device Enumerator Service was
   Stopped (StartType Manual) on Brian's machine — with it down, MTP devices
   never appear in the shell namespace at all. Now set to Automatic; the
   watcher should still detect the stopped state and report it.
5. **Freshness discipline.** Cache COM folder objects for at most one sync
   cycle; re-resolve the device from `NameSpace(17)` each cycle.

## Device identification

Match by capability, not name: a device qualifies if
`<storage>/Documents/TeamsTranscriber/` exists (the marker the Android app
creates; the desktop can also bootstrap it). Device display name ("MTP USB
Device") is driver-generic and useless.

## State left on the phone

`Documents/TeamsTranscriber/{outbox, library/meetings, sync}` — the real
contract tree, created during the spike and left in place.
