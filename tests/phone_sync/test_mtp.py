"""Tests for MtpTransport against a fake Shell.Application COM surface.

FakeShellFolder models the real quirks the spike doc (2026-07-24) recorded:
async CopyHere (items appear after `latency_polls` ParseName polls),
overwrite-onto-existing-name is a silent no-op (every push must delete
first), and optional invisible_names (a write lands in the store but
ParseName keeps returning None -- the session-cache hazard). It also
doubles as the "local directory" Shell.Application.NameSpace(<path>)
returns (real disk I/O, synchronous) since MtpTransport reaches local
folders via `root.Application.NameSpace(path)`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.phone_sync.mtp import (
    MtpNotReady,
    MtpStaleSession,
    MtpTransport,
    find_phone_root,
)


class FakeShellItem:
    """Duck-types a Shell FolderItem: .Name, .IsFolder, .IsFileSystem,
    .GetFolder, .ExtendedProperty('System.Size' / 'System.FileName').

    `filename` models spike doc B2 ("hide known extensions"): when set to
    something other than `name`, it's the *true* on-disk filename (with
    extension) returned by ExtendedProperty('System.FileName'), while
    `.Name` is the (possibly extension-stripped) display name a real Shell
    item would show. Defaults to `name` -- i.e. no stripping -- so existing
    fakes/tests that only ever set `.Name` are unaffected.
    """

    def __init__(self, name, *, folder=None, content=b"", owner=None,
                 is_file_system=True, filename=None):
        self.Name = name
        self._folder = folder
        self._content = content
        self._owner = owner  # FakeShellFolder this item currently lives in
        self.is_file_system = is_file_system
        self._filename = filename if filename is not None else name

    @property
    def IsFolder(self):
        return self._folder is not None

    @property
    def IsFileSystem(self):
        return self.is_file_system

    @property
    def GetFolder(self):
        return self._folder

    def ExtendedProperty(self, prop):
        if prop == "System.Size":
            return len(self._content)
        if prop == "System.FileName":
            return self._filename
        raise AssertionError(prop)


class FakeShellFolder:
    """Duck-types the Shell COM folder surface with MTP quirks:
    async CopyHere (items appear after `latency` polls), overwrite no-op,
    optional session-invisibility (item exists but ParseName returns None).

    Two modes: a device-tree folder (in-memory `_items` store, async
    CopyHere/MoveHere) when `local_path` is None, or a plain local
    directory (real disk I/O, synchronous) when `local_path` is set --
    what `shell.NameSpace(<path>)` returns for staging/discard/pull dirs.
    """

    def __init__(self, name="", *, shell=None, local_path=None,
                 latency_polls=0, invisible_names=None):
        self.Name = name
        self._shell = shell
        self._local_path = Path(local_path) if local_path is not None else None
        self._items: dict[str, FakeShellItem] = {}
        self._pending: dict[str, list] = {}
        self.latency_polls = latency_polls
        self.invisible_names = set(invisible_names or ())

    @property
    def Application(self):
        return self._shell

    def add_folder(self, name, **kw) -> FakeShellFolder:
        sub = FakeShellFolder(name, shell=self._shell, **kw)
        self._items[name] = FakeShellItem(name, folder=sub, owner=self)
        return sub

    def add_file(self, name, content=b"data", *, strip_ext=False) -> None:
        # strip_ext=True models spike doc B2: item.Name comes back
        # extension-less (Explorer "hide known extensions") while the true
        # on-disk name -- `name`, the dict key ParseName(leaf) looks up --
        # keeps its extension.
        display = name.rsplit(".", 1)[0] if strip_ext and "." in name else name
        self._items[name] = FakeShellItem(display, content=content, owner=self, filename=name)

    def Items(self):
        self._settle()
        return list(self._items.values())

    def ParseName(self, name):
        self._settle()
        if name in self.invisible_names:
            return None
        return self._items.get(name)

    def CopyHere(self, src) -> None:
        if self._shell is not None:
            self._shell.event_log.append(("copy_here", self.Name, Path(str(src)).name))
        if self._local_path is not None:
            # local mode: src is a FakeShellItem coming FROM the device.
            # Writes under the TRUE filename (B2) -- on a real device the
            # arrived local file keeps its extension even when item.Name
            # (the display name) doesn't.
            item = src
            (self._local_path / item._filename).write_bytes(item._content)
            return
        # device mode: src is a local path string (pushing local -> device).
        p = Path(src)
        if p.is_dir():
            if p.name in self._items:
                return
            self._land(p.name, FakeShellItem(
                p.name, folder=FakeShellFolder(p.name, shell=self._shell), owner=self,
            ))
            return
        if p.name in self._items:
            return  # overwrite silently no-ops (spike hazard #1)
        self._land(p.name, FakeShellItem(p.name, content=p.read_bytes(), owner=self))

    def MoveHere(self, item) -> None:
        if self._shell is not None:
            self._shell.event_log.append(("move_here", self.Name, item.Name))
        if self._local_path is not None:
            (self._local_path / item.Name).write_bytes(item._content)
        else:
            self._items[item.Name] = item
        if item._owner is not None:
            item._owner._items.pop(item.Name, None)
            item._owner._pending.pop(item.Name, None)

    def _land(self, name, item) -> None:
        if self.latency_polls > 0:
            self._pending[name] = [item, self.latency_polls]
        else:
            self._items[name] = item

    def _settle(self) -> None:
        for name in list(self._pending):
            item, remaining = self._pending[name]
            remaining -= 1
            if remaining <= 0:
                self._items[name] = item
                del self._pending[name]
            else:
                self._pending[name] = [item, remaining]


class FakeShell:
    """Duck-types Shell.Application: .NameSpace(17) -> This PC,
    .NameSpace(<local path>) -> a real-disk FakeShellFolder."""

    def __init__(self, this_pc: FakeShellFolder | None = None):
        self.this_pc = this_pc
        self.event_log: list[tuple[str, str, str]] = []
        self.namespace_log: list = []

    def NameSpace(self, target):
        self.namespace_log.append(target)
        if target == 17:
            return self.this_pc
        path = Path(target)
        path.mkdir(parents=True, exist_ok=True)
        return FakeShellFolder(str(path), shell=self, local_path=path)


def _make_tree(*, storages=1, with_marker=True, outbox_latency=0, outbox_invisible=None):
    """Builds a This-PC -> device -> storage -> Documents -> TeamsTranscriber
    tree. Returns (shell, marker_folder). marker_folder is None whenever the
    tree doesn't reach a usable TeamsTranscriber folder.

    storages=None -> no non-filesystem device at all ("no_device").
    storages=0    -> device present, zero storages ("device_not_ready").
    with_marker=False -> storage exists but no Documents/TeamsTranscriber
                          ("no_marker").
    """
    shell = FakeShell()
    this_pc = FakeShellFolder("This PC", shell=shell)
    shell.this_pc = this_pc
    this_pc._items["C:"] = FakeShellItem(
        "C:", folder=FakeShellFolder("C:", shell=shell), is_file_system=True,
    )
    if storages is None:
        return shell, None

    device_root = FakeShellFolder("Device", shell=shell)
    this_pc._items["Pixel"] = FakeShellItem(
        "Pixel", folder=device_root, is_file_system=False, owner=this_pc,
    )
    if storages == 0:
        return shell, None

    storage = device_root.add_folder("Internal shared storage")
    if not with_marker:
        return shell, None

    docs = storage.add_folder("Documents")
    marker = docs.add_folder("TeamsTranscriber")
    marker.add_folder("outbox", latency_polls=outbox_latency, invisible_names=outbox_invisible)
    lib = marker.add_folder("library")
    lib.add_folder("meetings")
    marker.add_folder("sync")
    return shell, marker


# --- find_phone_root -------------------------------------------------------


def test_find_phone_root_locates_marker():
    shell, marker = _make_tree()
    root = find_phone_root(shell=shell)
    assert root is marker
    assert root.Name == "TeamsTranscriber"


def test_find_phone_root_no_marker_raises():
    shell, _ = _make_tree(with_marker=False)
    with pytest.raises(MtpNotReady) as exc:
        find_phone_root(shell=shell)
    assert exc.value.reason == "no_marker"
    assert exc.value.hint


def test_find_phone_root_zero_storages_raises():
    shell, _ = _make_tree(storages=0)
    with pytest.raises(MtpNotReady) as exc:
        find_phone_root(shell=shell)
    assert exc.value.reason == "device_not_ready"
    assert exc.value.hint


def test_find_phone_root_no_device_raises():
    shell, _ = _make_tree(storages=None)
    with pytest.raises(MtpNotReady) as exc:
        find_phone_root(shell=shell)
    assert exc.value.reason == "no_device"
    assert exc.value.hint


def test_find_phone_root_skips_storageless_device_to_reach_marker():
    # A storageless peripheral enumerated BEFORE the phone must not
    # short-circuit the scan -- the ready phone behind it wins.
    shell, marker = _make_tree()
    this_pc = shell.this_pc
    empty_dev = FakeShellFolder("Storageless Peripheral", shell=shell)
    reordered = {"Peripheral": FakeShellItem(
        "Peripheral", folder=empty_dev, is_file_system=False, owner=this_pc,
    )}
    reordered.update(this_pc._items)
    this_pc._items = reordered  # storageless device now enumerates first
    root = find_phone_root(shell=shell)
    assert root is marker


def test_find_phone_root_all_storageless_raises_device_not_ready():
    shell, _ = _make_tree(storages=0)
    this_pc = shell.this_pc
    this_pc._items["Pixel2"] = FakeShellItem(
        "Pixel2", folder=FakeShellFolder("Device2", shell=shell),
        is_file_system=False, owner=this_pc,
    )
    with pytest.raises(MtpNotReady) as exc:
        find_phone_root(shell=shell)
    assert exc.value.reason == "device_not_ready"


# --- push --------------------------------------------------------------


def test_push_existing_name_deletes_then_copies(tmp_path):
    shell, root = _make_tree()
    outbox = root.ParseName("outbox").GetFolder
    outbox.add_file("rec_a.m4a", b"old-bytes")

    src = tmp_path / "new.m4a"
    src.write_bytes(b"new-bytes")

    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)
    t.push(src, "outbox/rec_a.m4a")

    kinds = [e[0] for e in shell.event_log if e[2] == "rec_a.m4a"]
    assert kinds == ["move_here", "copy_here"], shell.event_log
    assert outbox.ParseName("rec_a.m4a")._content == b"new-bytes"


def test_push_invisible_after_write_raises_stale_session(tmp_path):
    _shell, root = _make_tree(outbox_invisible={"rec_b.m4a"})
    src = tmp_path / "b.m4a"
    src.write_bytes(b"data")

    t = MtpTransport(root, poll_interval=0.005, timeout=0.05, pump=lambda: None)
    with pytest.raises(MtpStaleSession) as exc:
        t.push(src, "outbox/rec_b.m4a")
    assert "unplug and replug" in str(exc.value)


def test_push_succeeds_after_async_arrival_latency(tmp_path):
    # Spike: CopyHere is async -- the item only appears after some polls.
    _shell, root = _make_tree(outbox_latency=2)
    src = tmp_path / "slow.m4a"
    src.write_bytes(b"slow-bytes")

    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)
    t.push(src, "outbox/slow.m4a")

    outbox = root.ParseName("outbox").GetFolder
    assert outbox.ParseName("slow.m4a")._content == b"slow-bytes"


def test_push_creates_missing_parent_folder(tmp_path):
    _shell, root = _make_tree()
    src = tmp_path / "n.json"
    src.write_bytes(b"{}")

    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)
    t.push(src, "library/meetings/detail/n.json")

    meetings = root.ParseName("library").GetFolder.ParseName("meetings").GetFolder
    detail_item = meetings.ParseName("detail")
    assert detail_item is not None
    assert detail_item.GetFolder.ParseName("n.json")._content == b"{}"


# --- pull / read_text ----------------------------------------------------


def test_pull_and_read_text_round_trip(tmp_path):
    _shell, root = _make_tree()
    meetings = root.ParseName("library").GetFolder.ParseName("meetings").GetFolder
    meetings.add_file("5.json", b'{"ok": true}')

    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)

    dest = tmp_path / "pulled" / "5.json"
    t.pull("library/meetings/5.json", dest)
    assert dest.read_bytes() == b'{"ok": true}'

    assert t.read_text("library/meetings/5.json") == '{"ok": true}'
    assert t.read_text("library/meetings/missing.json") is None


def test_pull_renames_when_dest_leaf_differs_from_remote_leaf(tmp_path):
    # CopyHere keeps the source item's name; pull must rename to the
    # caller's requested destination filename when it differs.
    _shell, root = _make_tree()
    outbox = root.ParseName("outbox").GetFolder
    outbox.add_file("rec_d.m4a", b"renamed-bytes")

    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)
    dest = tmp_path / "local_copy.m4a"
    t.pull("outbox/rec_d.m4a", dest)

    assert dest.read_bytes() == b"renamed-bytes"
    assert not (tmp_path / "rec_d.m4a").exists()


def test_pull_finds_true_filename_when_display_name_is_stripped(tmp_path):
    # Spike doc B2 (device-verified): with Explorer's "hide known
    # extensions" on, item.Name comes back extension-less while the file
    # that actually lands locally keeps its extension. pull must wait on
    # the true filename (System.FileName), not item.Name, or it times out
    # waiting for a local file that will never appear under that name.
    _shell, root = _make_tree()
    outbox = root.ParseName("outbox").GetFolder
    outbox.add_file("rec_g.m4a", b"true-name-bytes", strip_ext=True)

    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)
    dest = tmp_path / "rec_g.m4a"
    t.pull("outbox/rec_g.m4a", dest)

    assert dest.read_bytes() == b"true-name-bytes"


def test_push_text_then_read_text(tmp_path):
    _shell, root = _make_tree()
    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)

    t.push_text('{"x": 1}', "sync/desktop_ack.json")
    assert t.read_text("sync/desktop_ack.json") == '{"x": 1}'


# --- delete ----------------------------------------------------------------


def test_delete_uses_move_here_and_removes_from_device(tmp_path):
    shell, root = _make_tree()
    outbox = root.ParseName("outbox").GetFolder
    outbox.add_file("rec_c.m4a", b"gone-soon")

    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)
    t.delete("outbox/rec_c.m4a")

    assert outbox.ParseName("rec_c.m4a") is None
    assert any(e[0] == "move_here" and e[2] == "rec_c.m4a" for e in shell.event_log)


def test_delete_missing_file_is_a_noop(tmp_path):
    _shell, root = _make_tree()
    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)
    t.delete("outbox/does_not_exist.m4a")  # must not raise


def test_delete_discard_staging_does_not_accumulate():
    # The per-call discard subdir (recording-sized files) must be cleaned
    # up after the move completes, not leak for the transport's lifetime.
    _shell, root = _make_tree()
    outbox = root.ParseName("outbox").GetFolder
    outbox.add_file("rec_e.m4a", b"e" * 64)
    outbox.add_file("rec_f.m4a", b"f" * 64)

    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)
    t.delete("outbox/rec_e.m4a")
    t.delete("outbox/rec_f.m4a")

    assert list(t._discard_dir.iterdir()) == []


# --- list_files --------------------------------------------------------


def test_list_files_recurses_with_relative_names_and_sizes():
    _shell, root = _make_tree()
    outbox = root.ParseName("outbox").GetFolder
    outbox.add_file("a.json", b"{}")
    outbox.add_file("b.m4a", b"xxxxx")
    meetings = root.ParseName("library").GetFolder.ParseName("meetings").GetFolder
    meetings.add_file("1.json", b"1234")

    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)

    lib_files = {f.name: f.size for f in t.list_files("library")}
    assert lib_files == {"library/meetings/1.json": 4}

    outbox_files = {f.name: f.size for f in t.list_files("outbox")}
    assert outbox_files == {"outbox/a.json": 2, "outbox/b.m4a": 5}


def test_list_files_uses_true_filename_when_display_name_is_stripped():
    # Spike doc B2 (device-verified): with Explorer's "hide known
    # extensions" on, item.Name is extension-less. If list_files built
    # RemoteFile.name from item.Name, the engine's endswith(".m4a") match
    # in run_sync would find nothing to import.
    _shell, root = _make_tree()
    outbox = root.ParseName("outbox").GetFolder
    outbox.add_file("rec_a.m4a", b"xxxxx", strip_ext=True)

    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)

    outbox_files = {f.name: f.size for f in t.list_files("outbox")}
    assert outbox_files == {"outbox/rec_a.m4a": 5}


def test_list_files_missing_prefix_returns_empty():
    _shell, root = _make_tree()
    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)
    assert t.list_files("does/not/exist") == []


# --- validate_name wiring ----------------------------------------------


@pytest.mark.parametrize("bad", [
    "../x", "outbox\\x", "/abs", "outbox/../x", "C:evil",
])
def test_operations_reject_invalid_names(tmp_path, bad):
    _shell, root = _make_tree()
    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)
    src = tmp_path / "f.txt"
    src.write_bytes(b"x")

    with pytest.raises(ValueError):
        t.list_files(bad)
    with pytest.raises(ValueError):
        t.push(src, bad)
    with pytest.raises(ValueError):
        t.pull(bad, tmp_path / "out.txt")
    with pytest.raises(ValueError):
        t.push_text("hi", bad)
    with pytest.raises(ValueError):
        t.read_text(bad)
    with pytest.raises(ValueError):
        t.delete(bad)


# --- lifecycle -----------------------------------------------------------


def test_close_cleans_up_tempdir():
    _shell, root = _make_tree()
    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: None)
    tmp_dir = Path(t._tmp)
    assert tmp_dir.exists()
    t.close()
    assert not tmp_dir.exists()


# --- async STA pump (B1) ----------------------------------------------------


def test_wait_invokes_injected_pump_once_per_poll_iteration(tmp_path):
    # Spike doc B1 (device-verified): async CopyHere/MoveHere only advance
    # while the calling STA thread pumps messages. _wait can't prove that
    # against a fake (there's no real COM async op to advance), but it must
    # provably call the injected pump on every iteration -- this is what
    # lets the real pythoncom.PumpWaitingMessages() default do its job.
    _shell, root = _make_tree(outbox_latency=3)
    src = tmp_path / "slow.m4a"
    src.write_bytes(b"pump-me")

    calls = []
    t = MtpTransport(root, poll_interval=0.001, timeout=1.0, pump=lambda: calls.append(None))
    t.push(src, "outbox/slow.m4a")

    # Landing takes 3 _settle() calls (one per predicate check), and the
    # pump runs before each predicate check -- so at least 3 pump calls.
    assert len(calls) >= 3
