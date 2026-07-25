"""MtpTransport: the Transport protocol implemented over Shell.Application
COM, against a phone connected via USB in MTP mode (no Android Auto / real
filesystem access -- the phone's storage is a Shell namespace, not a mounted
drive).

Every behavior here follows docs/superpowers/specs/2026-07-24-mtp-spike-findings.md
verbatim: delete-then-push (CopyHere onto an existing name silently no-ops),
verify-after-write with a poll loop that raises MtpStaleSession on timeout
(the Windows-side MTP session cache can be stale -- unplug/replug is the only
fix, not more retries), staged-arrival handling in find_phone_root (device
present with zero storages means "waiting for unlock / USB mode", not an
error), marker-based device matching (Documents/TeamsTranscriber existing is
what qualifies a device, not its display name), and per-cycle freshness (the
caller re-resolves the device root each sync cycle rather than caching it).

All COM access goes through duck-typed folder objects (`.Items()`,
`.ParseName(name)`, `.GetFolder`, `.CopyHere(path_or_item)`,
`.MoveHere(item)`, `.Application`) reachable from one lazy `_dispatch_shell()`
-- `win32com.client` is imported inside that function so this module (and
`find_phone_root`/`MtpTransport` with an injected fake) stays importable on
non-Windows/CI test environments that don't have pywin32.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

from teams_transcriber.phone_sync.transport import RemoteFile, validate_name

# Real type is a win32com Folder2/FolderItem COM wrapper; tests inject a
# duck-typed fake with the same surface. Kept as Any -- there is no shared
# base class between the two.
_ComFolder = Any

_THIS_PC = 17  # Shell.Application NameSpace() well-known folder id.


class MtpNotReady(RuntimeError):
    """The phone isn't reachable in a state MtpTransport can use yet.

    `.reason` is one of "service_stopped", "no_device", "device_not_ready",
    "no_marker" -- `.hint` is a human-readable next step for that reason.
    """

    def __init__(self, reason: str, *, hint: str) -> None:
        super().__init__(f"{reason}: {hint}")
        self.reason = reason
        self.hint = hint


class MtpStaleSession(RuntimeError):
    """A write or delete was verified missing/present after the poll
    timeout -- the Windows-side MTP session cache is stale (spike hazard
    #2: a successful write can be invisible to the session that made it).
    Recovery is unplug/replug, not more retries.
    """


def _dispatch_shell():
    # CopyHere/MoveHere are asynchronous; without the calling thread being
    # CoInitialize'd STA, the async op silently no-ops from a plain worker
    # thread (spike doc B1, device-verified). Idempotent -- CoInitialize
    # returns S_FALSE if this thread is already inited (e.g. the Qt main
    # thread), which is fine to ignore.
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass

    import win32com.client  # imported lazily: keeps this module importable
    # without pywin32 (non-Windows / CI test environments).

    return win32com.client.Dispatch("Shell.Application")


def _pump_messages() -> None:
    """Pump the calling thread's Windows message queue once. Async Shell
    ops (CopyHere/MoveHere) only advance while their originating STA thread
    pumps messages (spike doc B1) -- every `_wait` poll iteration must call
    this before checking its predicate. Safe no-op if pythoncom is
    unavailable or the thread isn't CoInitialized.
    """
    try:
        import pythoncom

        pythoncom.PumpWaitingMessages()
    except Exception:
        pass


def _require_wpd_service() -> None:
    """Raise MtpNotReady("service_stopped", ...) if the Portable Device
    Enumerator Service (WPDBusEnum) isn't running -- with it stopped, MTP
    devices never appear in the shell namespace at all (spike hazard #4).
    Any failure to query the service (missing pywin32, access denied,
    unknown service name) degrades to proceeding -- enumeration will then
    naturally report "no_device".
    """
    try:
        import win32service
        import win32serviceutil

        status = win32serviceutil.QueryServiceStatus("WPDBusEnum")
        state = status[1]
    except Exception:
        return
    if state != win32service.SERVICE_RUNNING:
        raise MtpNotReady(
            "service_stopped",
            hint="Start the 'Portable Device Enumerator Service' (WPDBusEnum) "
                 "— run 'Start-Service WPDBusEnum' as admin.",
        )


def _item_filename(item: Any) -> str:
    """The item's true on-disk filename, extension included.

    With Explorer's "hide known extensions" on, `item.Name` returns the
    extension-stripped display name (`rec_abc` for `rec_abc.m4a`) while the
    real file keeps its extension (spike doc B2, device-verified). The
    `System.FileName` extended property returns the true name; fall back to
    `.Name` if the property is missing or the call fails.
    """
    try:
        name = item.ExtendedProperty("System.FileName")
    except Exception:
        name = None
    return name if name else item.Name


def find_phone_root(shell=None) -> _ComFolder:
    """Locate <storage>/Documents/TeamsTranscriber on the first qualifying
    device. Devices are matched by capability (the marker folder existing),
    not by display name -- "MTP USB Device" is driver-generic and useless.
    """
    if shell is None:
        _require_wpd_service()
        shell = _dispatch_shell()
    this_pc = shell.NameSpace(_THIS_PC)
    devices = [i for i in this_pc.Items() if not i.IsFileSystem]
    if not devices:
        raise MtpNotReady("no_device", hint="Plug the phone in over USB.")
    saw_storageless = False
    for dev in devices:
        storages = list(dev.GetFolder.Items())
        if not storages:
            # Staged arrival (spike hazard #3): phone locked, or Android
            # reset USB mode to charging-only on this plug. Keep scanning --
            # the ready phone may sit behind another storageless peripheral.
            saw_storageless = True
            continue
        for storage in storages:
            docs = storage.GetFolder.ParseName("Documents")
            if docs is None:
                continue
            marker = docs.GetFolder.ParseName("TeamsTranscriber")
            if marker is not None:
                return marker.GetFolder
    if saw_storageless:
        raise MtpNotReady(
            "device_not_ready",
            hint="Unlock the phone and set USB to File transfer.",
        )
    raise MtpNotReady(
        "no_marker",
        hint="No TeamsTranscriber folder on the phone yet — install/run the "
             "companion app, or create Documents/TeamsTranscriber manually.",
    )


class MtpTransport:
    """Transport protocol (list_files/pull/push/push_text/read_text/delete)
    against the phone's TeamsTranscriber folder, over Shell COM.

    `root` is whatever `find_phone_root()` returned -- a duck-typed Folder
    object. Local-side folder objects (for pull's destination and delete's
    discard target) are reached via `root.Application.NameSpace(path)`,
    the same Shell.Application instance `find_phone_root` used, without
    MtpTransport needing its own `shell` parameter.

    Owns a TemporaryDirectory for stage-as-name push copies and the
    MoveHere discard target. Cleanup is via `weakref.finalize` (so a
    forgotten/GC'd transport doesn't leak a temp dir) plus an explicit
    `close()` for callers that want to release it deterministically at the
    end of a sync cycle.
    """

    def __init__(
        self,
        root: _ComFolder,
        *,
        poll_interval: float = 0.2,
        timeout: float = 30.0,
        pump: Callable[[], None] | None = None,
    ) -> None:
        self._root = root
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._pump = pump if pump is not None else _pump_messages
        self._shell = root.Application
        self._tmpdir = tempfile.TemporaryDirectory(prefix="tt-mtp-")
        self._tmp = self._tmpdir.name
        self._stage_root = Path(self._tmp) / "stage"
        self._stage_root.mkdir(parents=True, exist_ok=True)
        self._discard_dir = Path(self._tmp) / "discard"
        self._discard_dir.mkdir(parents=True, exist_ok=True)
        self._finalizer = weakref.finalize(self, self._tmpdir.cleanup)

    def close(self) -> None:
        self._finalizer()

    # --- Transport protocol ------------------------------------------------

    def list_files(self, prefix: str) -> list[RemoteFile]:
        validate_name(prefix)
        folder = self._resolve_folder(prefix)
        if folder is None:
            return []
        out: list[RemoteFile] = []
        self._walk(folder, prefix, out)
        return sorted(out, key=lambda f: f.name)

    def pull(self, name: str, dest: Path) -> None:
        validate_name(name)
        folder, leaf = self._resolve_parent(name, create=False)
        item = None if folder is None else folder.ParseName(leaf)
        if item is None:
            raise FileNotFoundError(name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        local_folder = self._shell.NameSpace(str(dest.parent))
        local_folder.CopyHere(item)
        # CopyHere keeps the source item's name -- rename to the caller's
        # requested dest filename if it differs from the remote leaf name.
        # Use the true filename (B2), not item.Name, which is extension-
        # stripped when Explorer hides known extensions.
        arrived = dest.parent / _item_filename(item)
        self._wait(
            lambda: arrived.exists(),
            f"pulled {name} never arrived locally",
        )
        if arrived != dest:
            arrived.replace(dest)

    def push(self, src: Path, name: str) -> None:
        validate_name(name)
        folder, leaf = self._resolve_parent(name, create=True)
        existing = folder.ParseName(leaf)
        if existing is not None:
            self._move_to_discard(folder, existing)  # overwrite no-ops (spike #1)
        stage_dir = Path(tempfile.mkdtemp(dir=self._stage_root))
        try:
            staged = self._stage_as(src, leaf, stage_dir)  # temp copy named `leaf`
            folder.CopyHere(str(staged))
            self._wait(
                lambda: folder.ParseName(leaf) is not None,
                f"pushed {name} never appeared",
            )
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)

    def push_text(self, text: str, name: str) -> None:
        validate_name(name)
        fd, tmp_path = tempfile.mkstemp(dir=self._tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            self.push(Path(tmp_path), name)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def read_text(self, name: str) -> str | None:
        validate_name(name)
        folder, leaf = self._resolve_parent(name, create=False)
        item = None if folder is None else folder.ParseName(leaf)
        if item is None:
            return None
        stage_dir = Path(tempfile.mkdtemp(dir=self._stage_root))
        try:
            dest = stage_dir / leaf
            self.pull(name, dest)
            return dest.read_text(encoding="utf-8")
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)

    def delete(self, name: str) -> None:
        validate_name(name)
        folder, leaf = self._resolve_parent(name, create=False)
        item = None if folder is None else folder.ParseName(leaf)
        if item is None:
            return
        self._move_to_discard(folder, item)

    # --- internals -----------------------------------------------------------

    def _wait(self, predicate: Callable[[], bool], message: str) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            # Pump before checking -- async CopyHere/MoveHere only advance
            # while their originating STA thread pumps messages (spike B1).
            self._pump()
            if predicate():
                return
            if time.monotonic() >= deadline:
                raise MtpStaleSession(f"{message} — unplug and replug the phone.")
            time.sleep(self.poll_interval)

    def _move_to_discard(self, folder: _ComFolder, item: Any) -> None:
        # A fresh subdirectory per call -- MoveHere onto a local path that
        # already holds a same-named discard (e.g. this name got pushed and
        # discarded again in a later cycle on a long-lived transport) can
        # pop a native "overwrite?" confirmation dialog and hang.
        # Use the true filename (B2), not item.Name, which is extension-
        # stripped when Explorer hides known extensions -- otherwise the
        # absence poll below checks a name that was never valid and passes
        # spuriously regardless of whether the move actually completed.
        name = _item_filename(item)
        discard_dir = Path(tempfile.mkdtemp(dir=self._discard_dir))
        try:
            discard_folder = self._shell.NameSpace(str(discard_dir))
            discard_folder.MoveHere(item)
            self._wait(
                lambda: folder.ParseName(name) is None,
                f"{name} still present after discard",
            )
        finally:
            # Device-side absence means the move's local-copy leg completed;
            # drop the recording-sized discard copy instead of accumulating
            # it in %TEMP% for the transport's lifetime.
            shutil.rmtree(discard_dir, ignore_errors=True)

    def _stage_as(self, src: Path, leaf: str, stage_dir: Path) -> Path:
        # CopyHere keeps the source path's own filename, so the pushed
        # file must be staged under a local copy already named `leaf`.
        dest = stage_dir / leaf
        shutil.copy2(src, dest)
        return dest

    def _create_subfolder(self, folder: _ComFolder, part: str) -> _ComFolder:
        local_dir = self._stage_root / "mkdir" / part
        local_dir.mkdir(parents=True, exist_ok=True)
        folder.CopyHere(str(local_dir))
        self._wait(
            lambda: folder.ParseName(part) is not None,
            f"folder {part} never appeared",
        )
        return folder.ParseName(part).GetFolder

    def _resolve_parent(self, name: str, *, create: bool) -> tuple[_ComFolder | None, str]:
        """Walk all but the last '/'-separated component of `name` from the
        root. Returns (None, leaf) if an intermediate folder is missing and
        create=False; missing folders are created (spike's "create folder"
        row) when create=True.
        """
        parts = name.split("/")
        folder = self._root
        for part in parts[:-1]:
            nxt = folder.ParseName(part)
            if nxt is None:
                if not create:
                    return None, parts[-1]
                folder = self._create_subfolder(folder, part)
            else:
                folder = nxt.GetFolder
        return folder, parts[-1]

    def _resolve_folder(self, path: str) -> _ComFolder | None:
        folder = self._root
        if not path:
            return folder
        for part in path.split("/"):
            nxt = folder.ParseName(part)
            if nxt is None:
                return None
            folder = nxt.GetFolder
        return folder

    def _walk(self, folder: _ComFolder, rel_prefix: str, out: list[RemoteFile]) -> None:
        for item in folder.Items():
            # True filename (B2) -- item.Name is extension-stripped when
            # Explorer hides known extensions, which would break the
            # engine's endswith(".m4a")/(".json") matching downstream.
            item_name = _item_filename(item)
            rel_name = f"{rel_prefix}/{item_name}" if rel_prefix else item_name
            if item.IsFolder:
                self._walk(item.GetFolder, rel_name, out)
            else:
                size = int(item.ExtendedProperty("System.Size") or 0)
                out.append(RemoteFile(name=rel_name, size=size))
