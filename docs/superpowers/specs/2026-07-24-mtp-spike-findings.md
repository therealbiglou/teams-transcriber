# MTP Spike Findings (Android Companion Phase 2)

**Date:** 2026-07-24 · **Device:** Brian's Pixel (VID_18D1/PID_4EE1, "MTP USB
Device") · **Method:** Shell.Application COM. Sections marked **[DEVICE
RUN]** were found during the Task-6 real-device verification with the actual
`MtpTransport` (pywin32), not the initial PowerShell spike — they are the
bugs the test fakes could not model.

## Access path

`Shell.Application` → `NameSpace(17)` (This PC) → the item with
`IsFileSystem == False` is the device → `.GetFolder.Items()` are storages
("Internal shared storage") → traverse by repeated
`folder.ParseName(name).GetFolder`. All enumeration is sub-second.

## [DEVICE RUN] Two blocking bugs the fakes hid

### B1. Async Shell ops need STA + a message pump (from Python)

`CopyHere`/`MoveHere` are asynchronous. From PowerShell's console host they
"just work" because it runs an STA message pump. From a plain pywin32 call on
a worker thread they **silently no-op** — the file never appears, and the
operation's `_wait` times out into a spurious `MtpStaleSession`. The fix, and
the only thing that makes MtpTransport work off the GUI thread:

- The thread that touches the shell must `pythoncom.CoInitialize()` (STA)
  before dispatching `Shell.Application`.
- Every poll-wait after a CopyHere/MoveHere must call
  `pythoncom.PumpWaitingMessages()` each iteration to advance the async copy.

With the pump, push landed in ~1.6 s (vs ~250 ms in PowerShell), pull and
delete completed similarly. Without it, zero progress.

### B2. `item.Name` omits the extension (Explorer "hide known extensions")

When the user has "hide extensions for known file types" enabled (default on
this machine), the Shell namespace's `.Name` / `System.ItemNameDisplay`
return the **extension-less** display name (`rec_abc`), while the actual
file — on the phone and when pulled to disk — keeps its extension
(`rec_abc.m4a`). Consequences if `.Name` is used to reconstruct a filename:

- `pull`'s `arrived = dest.parent / item.Name` waits for the wrong local
  name → timeout.
- `list_files` builds `RemoteFile.name` from `.Name` → returns `rec_abc`,
  and the engine's `run_sync` matches `n.endswith(".m4a")` → **nothing
  imports**.

Fix: read the true filename via `item.ExtendedProperty("System.FileName")`
(returns `rec_abc.m4a`; falls back to `.Name` if the property is missing).
`ParseName("rec_abc.m4a")` (full name) still matches correctly, so the
push-visibility poll keyed on the leaf is fine — only name *reconstruction*
sites need the property. `item.Path` is a CLSID/GUID string, never a usable
filesystem path.

## Operations (verified against the real phone)

| Operation | Mechanism | Notes |
|---|---|---|
| List + sizes | `folder.Items()`; `ExtendedProperty('System.Size')`; names via `ExtendedProperty('System.FileName')` (B2) | instant |
| Push | delete-existing → `CopyHere(localPath)` → pump+poll `ParseName(leaf)` | ~1.6 s w/ pump |
| Pull | `localShellFolder.CopyHere(item)` → pump+poll for `System.FileName` locally | byte-accurate |
| Delete (silent) | `localDiscardShellFolder.MoveHere(item)` → pump+poll device absence | no confirmation UI |
| Create folder | `parent.CopyHere(localFolderPath)` | ~250 ms |

## Hazards (from the initial spike; still apply)

1. **Overwrite silently no-ops.** `CopyHere` onto an existing name neither
   overwrites nor errors. Every push is delete-then-push.
2. **Session-cache invisibility.** A write can be invisible to the session
   that made it; verify-after-write and, on persistent invisibility, raise a
   replug-guidance error rather than retrying forever.
3. **Staged arrival.** After (re)connect the device may enumerate with zero
   storages: phone locked, or Android reset USB mode to charging (per-plug
   unless Developer Options → Default USB configuration = File transfer).
   Treat device-without-storage as "waiting" and surface unlock guidance.
4. **WPDBusEnum service.** Must be Running or MTP devices never appear in the
   shell namespace. Now set to Automatic.
5. **Freshness discipline.** Cache COM folder objects for at most one sync
   cycle; re-resolve from `NameSpace(17)` each cycle.

## Device identification

Match by capability, not name: a device qualifies if
`<storage>/Documents/TeamsTranscriber/` exists. Device display name ("MTP
USB Device") is driver-generic.
