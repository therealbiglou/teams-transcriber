# Phone Sync USB Experience (Android Companion Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plug the phone in and the desktop syncs it — an `MtpTransport` built on the spike-verified Shell COM patterns, a background device watcher that runs the Phase-1 engine when the phone's `TeamsTranscriber` folder appears, a Settings toggle + status, and the engine-hardening items ledgered for Phase 2.

**Architecture:** `phone_sync/mtp.py` implements the existing `Transport` protocol over Shell COM via a thin `_ShellSession` seam (fakeable in tests; the real COM layer is exercised by a manual smoke script plus a controller-run device check at branch end). `phone_sync/watcher.py` polls for the device marker on a daemon thread and runs `run_sync` once per device arrival; `App` wires it to the pipeline, the Wrike close-loop, toasts, and a new Settings → Integrations section. Every MTP behavior encodes a finding from `docs/superpowers/specs/2026-07-24-mtp-spike-findings.md` (read it before Task 3).

**Tech Stack:** Python 3.11, pywin32 (`win32com.client`) — already a dependency, PySide6 only at the App/Settings layer, pytest offscreen.

## Global Constraints

- uv-only tooling (`uv run pytest`; fallback `.venv\Scripts\python.exe -m pytest`); Bash tool for git. Conventional commits; every commit message ends with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_018PYRS2ZXeA6Z4ra2xkRsDz`
- Spike findings are binding (spec `2026-07-24-mtp-spike-findings.md`): push is delete-then-push (overwrite silently no-ops); delete is `MoveHere` into a local discard dir (silent); writes are verified afterward and persistent invisibility raises a replug-guidance error; device-without-storage means locked/charging (wait + guide, don't fail); device matching is by the `Documents/TeamsTranscriber` marker folder, never by device name; COM objects are cached for at most one sync cycle.
- The sync contract and engine rules from Phase 1 are unchanged; `run_sync`'s signature is untouched. MTP work happens only on the watcher thread — never the Qt main thread; UI updates hop via the 3-arg `QTimer.singleShot(0, <qobject>, callable)` pattern.
- No `QMessageBox`, no OS toasts — `show_in_app_toast` only. Settings persist via `settings._raw["integrations"]` + `save_settings`, mirroring the Wrike keys.
- Run the full suite before each commit (~586 tests at branch start must stay green).

---

### Task 1: Engine hardening (ledgered Phase-2 ride-alongs)

**Files:**
- Modify: `src/teams_transcriber/phone_sync/transport.py` (path-traversal guard)
- Modify: `src/teams_transcriber/phone_sync/sync.py` (started_at UTC normalization; stray library detail-file pruning)
- Test: `tests/phone_sync/test_transport.py`, `tests/phone_sync/test_sync.py`

**Interfaces:**
- Produces: `transport.validate_name(name: str) -> str` (module-level; returns the name or raises `ValueError` on absolute paths, drive letters, backslashes, or `..` segments); `LocalDirTransport._path` calls it. `run_sync` gains no new parameters — pruning happens inside the export step.
- Consumes: existing `run_sync`, `build_library`, `Transport`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/phone_sync/test_transport.py`:

```python
import pytest

from teams_transcriber.phone_sync.transport import validate_name


@pytest.mark.parametrize("bad", [
    "../escape.m4a", "outbox/../../x", "C:/evil", "C:\\evil",
    "outbox\\rec.m4a", "/abs/path", "outbox/../x",
])
def test_validate_name_rejects_traversal_and_separators(bad):
    with pytest.raises(ValueError):
        validate_name(bad)


def test_validate_name_accepts_contract_names():
    for good in ("outbox/rec_a.m4a", "library/meetings/5.json", "sync/desktop_ack.json"):
        assert validate_name(good) == good


def test_local_transport_refuses_bad_names(tmp_path):
    from teams_transcriber.phone_sync.transport import LocalDirTransport
    t = LocalDirTransport(tmp_path)
    with pytest.raises(ValueError):
        t.read_text("../outside.txt")
```

Append to `tests/phone_sync/test_sync.py` (reuse its existing helpers: `phone` transport fixture, `_fake_import`, seeding per `_seed_recording_with_todo` / library-export test patterns):

```python
def test_started_at_with_nonutc_offset_normalized(db, tmp_path):
    # Sidecar with started_at "2026-07-24T20:30:00+05:30" → the imported
    # recording's started_at must be the UTC equivalent "2026-07-24T15:00:00+00:00".
    # Assert on the datetime the fake importer received: fromisoformat(...)
    # .utcoffset() == timedelta(0) and the instant equals the original.
    ...


def test_stray_library_detail_file_pruned(db, tmp_path):
    # Pre-place library/meetings/999.json on the transport (a deleted
    # recording's leftover). Seed one real summarized recording rid.
    # run_sync → transport contains library/meetings/<rid>.json but
    # NOT 999.json; manifest/meetings.json still written.
    ...
```

Write both in full from the file's existing seeding helpers.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/phone_sync -v -k "validate_name or nonutc or stray"`
Expected: FAIL (`validate_name` missing; naive passthrough keeps +05:30; stray file survives).

- [ ] **Step 3: Implement**

`transport.py` — add module-level:

```python
def validate_name(name: str) -> str:
    """Reject names that could escape the phone folder. Phase 2 feeds the
    transport names listed from the phone itself, so they are untrusted."""
    if "\\" in name or name.startswith("/") or ":" in name:
        raise ValueError(f"invalid transport name: {name!r}")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"invalid transport name: {name!r}")
    return name
```

and call it first in `LocalDirTransport._path`.

`sync.py` — `_parse_started_at` normalizes to UTC:

```python
def _parse_started_at(sidecar: contract.Sidecar) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(sidecar.started_at)
    except ValueError:
        return None
    # Contract requires aware timestamps; normalize any offset to UTC so
    # recordings.started_at stays lexicographically ordered (ORDER BY).
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
```

(`from datetime import UTC, datetime` at top.) In the export step, prune strays before pushing:

```python
    # --- export + ack --------------------------------------------------------
    library_files = build_library(db, now_iso=now_iso)
    # Prune detail files for recordings that no longer exist (deleted on the
    # desktop) — "regenerated every sync" means replaced, not accreted.
    current = set(library_files)
    for remote_file in transport.list_files("library"):
        if remote_file.name not in current:
            transport.delete(remote_file.name)
    for name, text in library_files.items():
        transport.push_text(text, name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/phone_sync -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/phone_sync/transport.py src/teams_transcriber/phone_sync/sync.py tests/phone_sync/
git commit -m "feat(phone-sync): name validation, UTC normalization, stale library pruning"
```

---

### Task 2: Schema v8 — `toggled_at` column for a durable LWW baseline

**Files:**
- Create: `src/teams_transcriber/storage/schema_v8.py`
- Modify: `src/teams_transcriber/storage/todos.py` (`mark_done` stamps `toggled_at`; model + row mapper)
- Modify: `src/teams_transcriber/storage/models.py` (`TodoState.toggled_at: str | None`)
- Modify: `src/teams_transcriber/storage/__init__.py` (register `SCHEMA_V8`)
- Modify: `src/teams_transcriber/phone_sync/sync.py` (LWW compares `toggled_at or done_at`)
- Test: `tests/storage/test_todos.py`, `tests/storage/test_schema_v8_migration.py` (new), `tests/phone_sync/test_sync.py`

**Interfaces:**
- Produces: `SCHEMA_V8 = Migration(version=8, name="add todo_state.toggled_at", apply=...)` (single `ALTER TABLE todo_state ADD COLUMN toggled_at TEXT`); `TodoState.toggled_at`; `mark_done` sets `toggled_at` on EVERY call (done or undone — `done_at_override` when given, else wall-clock now), while `done_at` semantics are unchanged.
- Rationale (final-review triage item): un-checking a todo clears `done_at`, so the row lost its LWW baseline and a stale phone re-send of an *older* done-toggle would wrongly re-apply. `toggled_at` survives un-checking.

- [ ] **Step 1: Write the failing tests**

`tests/storage/test_schema_v8_migration.py` — mirror `tests/storage/test_schema_v5_migration.py`'s structure: build a db, assert `toggled_at` column exists via `PRAGMA table_info(todo_state)`, and that pre-existing rows read back with `toggled_at is None`.

Append to `tests/storage/test_todos.py`:

```python
def test_mark_done_stamps_toggled_at_even_when_undone(db_or_equivalent_fixture):
    # mark_done(..., done=True) → toggled_at set, done_at set
    # mark_done(..., done=False) → done_at None but toggled_at STILL updated
    ...


def test_mark_done_override_sets_both(db_or_equivalent_fixture):
    # done_at_override="2026-07-24T10:00:00+00:00" → done_at AND toggled_at
    # equal the override
    ...
```

Append to `tests/phone_sync/test_sync.py`:

```python
def test_undone_row_keeps_lww_baseline(db, tmp_path):
    # Desktop: todo checked then UN-checked at 12:00 (mark_done False —
    # toggled_at 12:00, done_at None). Phone sends a STALE done=True with
    # toggled_at 11:00. run_sync → toggle SKIPPED as stale (toggles_skipped_stale
    # == 1), todo remains un-done. Pre-v8 this wrongly re-applied.
    ...
```

Write all in full from existing fixtures.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/storage tests/phone_sync -v -k "v8 or toggled_at or undone_row"`
Expected: FAIL (no column/attribute; stale toggle re-applies).

- [ ] **Step 3: Implement**

`schema_v8.py` (mirror schema_v2's single-ALTER shape):

```python
"""v8: todo_state.toggled_at — durable LWW baseline that survives un-checking."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE todo_state ADD COLUMN toggled_at TEXT")


SCHEMA_V8 = Migration(version=8, name="add todo_state.toggled_at", apply=_apply)
```

`models.py`: add `toggled_at: str | None` to `TodoState`. `todos.py`: `_row_to_todo` maps it; `mark_done` computes `stamp = done_at_override or datetime.now(UTC).isoformat()` and writes `toggled_at = stamp` on both the INSERT and UPDATE paths (with `done_at` handling unchanged); `upsert` and `seed` set `toggled_at` the same way `done_at` is handled there today (upsert: stamp on write; seed: leave untouched on conflict, NULL on fresh insert). Register `SCHEMA_V8` after `SCHEMA_V7`.

`sync.py` LWW: replace the `current.done_at` baseline with:

```python
        baseline = current.toggled_at or current.done_at
        if baseline is not None and _newer_or_equal(baseline, change.toggled_at):
            report.toggles_skipped_stale += 1
            continue
```

where `_newer_or_equal(a, b)` is the existing datetime-with-string-fallback comparison extracted into a tiny helper (keep the `suppress(ValueError, TypeError)` semantics).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/storage tests/phone_sync -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/storage/ src/teams_transcriber/phone_sync/sync.py tests/
git commit -m "feat(storage): toggled_at LWW baseline that survives un-checking (schema v8)"
```

---

### Task 3: `MtpTransport` — the Transport protocol over Shell COM

**Files:**
- Create: `src/teams_transcriber/phone_sync/mtp.py`
- Create: `scripts/mtp_smoke.py`
- Test: `tests/phone_sync/test_mtp.py` (new — against a fake shell)

**Interfaces:**
- Produces:
  - `class MtpNotReady(RuntimeError)` with `.reason: str` ∈ `{"service_stopped", "no_device", "device_not_ready", "no_marker"}` and a human `.hint` (e.g. "Unlock the phone and set USB to File transfer").
  - `class MtpStaleSession(RuntimeError)` — write verified missing; hint: unplug/replug.
  - `find_phone_root(shell=None) -> _ComFolder` — locates `<storage>/Documents/TeamsTranscriber` on the first qualifying device (marker-based per the spike doc) or raises `MtpNotReady`.
  - `class MtpTransport` implementing the full `Transport` protocol (`list_files/pull/push/push_text/read_text/delete`) against that root. Constructor: `MtpTransport(root, *, poll_interval=0.2, timeout=30.0)`. Every name goes through `transport.validate_name` (Task 1).
  - Internals follow the spike verbatim: push = delete-existing (if present) → `CopyHere(local)` → poll `ParseName` → on timeout raise `MtpStaleSession`; pull = local shell `CopyHere(item)` → poll local path; delete = `MoveHere` into a per-transport local discard tempdir → poll device absence; `push_text`/`read_text` via temp files; `list_files` recurses folders, sizes via `ExtendedProperty('System.Size')`, names as forward-slash paths relative to the root.
  - The COM seam: all shell access goes through duck-typed folder objects (`.Items()`, `.ParseName(name)`, `.GetFolder`, `.CopyHere(path)`, `.MoveHere(item)`) plus one module-level `_dispatch_shell()` (imports `win32com.client` lazily — keeps non-Windows test envs importable). `find_phone_root(shell=...)` and `MtpTransport(root)` accept fakes.
- Consumes: `Transport` protocol shape (Task 4 of Phase 1), `validate_name` (Task 1).

- [ ] **Step 1: Write the failing tests**

Create `tests/phone_sync/test_mtp.py` with a `FakeShellFolder` test double that models the spike's observed semantics — this is the core of the task's test value, write it carefully:

```python
class FakeShellFolder:
    """Duck-types the Shell COM folder surface with MTP quirks:
    async CopyHere (items appear after `latency` polls), overwrite no-op,
    optional session-invisibility (item exists but ParseName returns None)."""
```

with knobs: `latency_polls`, `invisible_names: set[str]`, and file store dicts. Tests (write in full):

- `find_phone_root`: fake This-PC hierarchy → device with marker found; no marker → `MtpNotReady(reason="no_marker")`; device with zero storages → `reason="device_not_ready"`; no non-filesystem device → `"no_device"`.
- `push` on existing name deletes first (fake records the delete-then-copy order) and content updates (overwrite-no-op quirk is bypassed).
- `push` whose copy lands but stays invisible (`invisible_names`) → `MtpStaleSession` after timeout (use a tiny timeout).
- `pull` + `read_text` round-trip through the fake.
- `delete` uses MoveHere semantics (fake records it; device store loses the item).
- `list_files` recurses and emits root-relative forward-slash names + sizes.
- all operations reject invalid names (`validate_name` wired).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/phone_sync/test_mtp.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement `mtp.py`**

Full implementation per the Interfaces block. Key excerpts the implementer must match (complete the rest from the spike doc's table):

```python
def find_phone_root(shell=None):
    if shell is None:
        _require_wpd_service()
        shell = _dispatch_shell()
    this_pc = shell.NameSpace(17)
    devices = [i for i in this_pc.Items() if not i.IsFileSystem]
    if not devices:
        raise MtpNotReady("no_device", hint="Plug the phone in over USB.")
    for dev in devices:
        storages = list(dev.GetFolder.Items())
        if not storages:
            raise MtpNotReady(
                "device_not_ready",
                hint="Unlock the phone and set USB to File transfer.",
            )
        for storage in storages:
            docs = storage.GetFolder.ParseName("Documents")
            if docs is None:
                continue
            marker = docs.GetFolder.ParseName("TeamsTranscriber")
            if marker is not None:
                return marker.GetFolder
    raise MtpNotReady(
        "no_marker",
        hint="No TeamsTranscriber folder on the phone yet — install/run the "
             "companion app, or create Documents/TeamsTranscriber manually.",
    )
```

`_require_wpd_service()` checks `WPDBusEnum` via `win32serviceutil.QueryServiceStatus` (pywin32) and raises `MtpNotReady("service_stopped", hint="Start the 'Portable Device Enumerator Service' (WPDBusEnum) — run 'Start-Service WPDBusEnum' as admin.")` when not running; wrapped in try/except so query failures degrade to proceeding (the enumeration itself will then report no_device).

Push (the delete-then-push + verify pattern):

```python
    def push(self, src: Path, name: str) -> None:
        validate_name(name)
        folder, leaf = self._resolve_parent(name, create=True)
        existing = folder.ParseName(leaf)
        if existing is not None:
            self._move_to_discard(folder, existing)   # overwrite no-ops (spike #1)
        staged = self._stage_as(src, leaf)            # temp copy named `leaf`
        folder.CopyHere(str(staged))
        self._wait(lambda: folder.ParseName(leaf) is not None,
                   f"pushed {name} never appeared (stale MTP session)")
```

`_wait` polls at `poll_interval` up to `timeout` and raises `MtpStaleSession(message + " — unplug and replug the phone.")` on expiry. `_resolve_parent(..., create=True)` creates missing intermediate folders by copying a staged local directory tree and verifying visibility (spike hazard #2). `_stage_as` copies into the transport's tempdir so the pushed file carries the target leaf name (CopyHere keeps source names).

`scripts/mtp_smoke.py`: a runnable manual check — find root, list all three contract dirs, push/pull/delete a probe file in `sync/`, print timings; exits non-zero with the `MtpNotReady.hint` when not ready. ~40 lines, no test needed (it IS the manual test).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/phone_sync -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/phone_sync/mtp.py scripts/mtp_smoke.py tests/phone_sync/test_mtp.py
git commit -m "feat(phone-sync): MtpTransport over Shell COM per spike findings"
```

---

### Task 4: Device watcher + App wiring (incl. Wrike close-loop hookup)

**Files:**
- Create: `src/teams_transcriber/phone_sync/watcher.py`
- Modify: `src/teams_transcriber/ui/app.py` (start/stop watcher, sync callbacks, toasts)
- Test: `tests/phone_sync/test_watcher.py` (new), `tests/ui/test_app_phone_sync.py` (new)

**Interfaces:**
- Produces:
  - `class PhoneSyncWatcher` — plain daemon thread (`start()`, `stop()`), constructor
    `PhoneSyncWatcher(*, run_cycle: Callable[[], None], probe: Callable[[], bool], poll_seconds: float = 5.0, on_error: Callable[[str], None] | None = None)`.
    Loop: when `probe()` flips False→True (device arrived), call `run_cycle()` once; don't re-run until the probe has gone False (device left) and True again — one sync per plug-in. `probe` exceptions of type `MtpNotReady` with reason `device_not_ready` surface ONCE per arrival via `on_error` (the unlock hint), other reasons are silent-waiting states.
  - `App._phone_sync_probe() -> bool` (find_phone_root succeeds), `App._phone_sync_cycle()` (build `MtpTransport(find_phone_root())`, call `run_sync(self.db, transport, import_recording=self.pipeline.import_phone_recording, on_todos_changed=self._on_phone_todos_changed, now_iso=...)`, toast the report, persist last-sync status into settings), `App._on_phone_todos_changed(rid)` = history refresh + master reload + `_wrike_close_loop_sync(rid)` (closes the ledgered Phase-1 gap where CLI passed None).
  - Watcher runs ONLY when `settings._raw["integrations"]["phone_sync_enabled"]` is true; `App._apply_phone_sync_setting()` starts/stops it and is called at startup and after Settings save.
  - All toasts/UI hops from the watcher thread use `QTimer.singleShot(0, self.window, ...)`.
- Consumes: `find_phone_root`/`MtpTransport`/`MtpNotReady` (Task 3), `run_sync` (Phase 1), `_wrike_close_loop_sync`, `show_in_app_toast`.

- [ ] **Step 1: Write the failing tests**

`tests/phone_sync/test_watcher.py` (pure threading, no Qt): fake probe/run_cycle callables with events —
- arrival triggers exactly one cycle; probe staying True doesn't re-trigger; False→True re-triggers.
- `stop()` joins promptly (< 2s) even mid-poll.
- `device_not_ready` from probe → `on_error` called once per arrival, cycle not run.
Write in full with `threading.Event` gates and generous timeouts (use `poll_seconds=0.05`).

`tests/ui/test_app_phone_sync.py` (mirror `tests/ui/test_app_wrike_close_loop.py`'s `App.__new__` + SimpleNamespace pattern):
- `_on_phone_todos_changed(42)` refreshes history + reloads master todos + calls a stubbed `_wrike_close_loop_sync(42)`.
- `_phone_sync_cycle` with a monkeypatched `find_phone_root`/`MtpTransport`/`run_sync` (return a canned `PhoneSyncReport`) persists last-sync status into `settings._raw["integrations"]` and requests a toast (capture via monkeypatched `show_in_app_toast` after the singleShot hop — use `qtbot.waitUntil`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/phone_sync/test_watcher.py tests/ui/test_app_phone_sync.py -v`
Expected: FAIL with ImportError/AttributeError.

- [ ] **Step 3: Implement**

`watcher.py` (~60 lines): daemon thread; loop `while not self._stop.wait(self.poll_seconds)`; track `was_present: bool` and `errored_this_arrival: bool`; call probe inside try/except `MtpNotReady` (reason `device_not_ready` → on_error once, treat as absent; other reasons → absent silently); on False→True run `run_cycle()` inside try/except Exception (log + on_error, still counts as consumed arrival).

`app.py`: in `__init__` after the tray wiring — `self._phone_watcher: PhoneSyncWatcher | None = None; self._apply_phone_sync_setting()`. Implement the three methods per the Interfaces block; `_phone_sync_cycle` runs entirely on the watcher thread, builds the toast text like the CLI's summary line, persists `{"phone_sync_last": {"at": now_iso, "ok": not report.failures, "summary": text}}` under `integrations` via `save_settings`, and hops `show_in_app_toast("Phone sync", text)` to the main thread. `_apply_phone_sync_setting` also runs after `_open_settings_tab`'s dialog saves (hook the existing `dlg.saved` connection point).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/phone_sync tests/ui/test_app_phone_sync.py -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/phone_sync/watcher.py src/teams_transcriber/ui/app.py tests/
git commit -m "feat(phone-sync): device watcher wired to pipeline, toasts, and Wrike close-loop"
```

---

### Task 5: Settings → Integrations "Phone sync" section

**Files:**
- Modify: `src/teams_transcriber/ui/settings_dialog.py` (`_build_integrations_tab`, `_on_accept`)
- Test: `tests/ui/test_settings_integrations_tab.py`

**Interfaces:**
- Produces: `self.phone_sync_enable_cb: QCheckBox` ("Sync my phone automatically when it's plugged in (USB file transfer)"), a selectable status QLabel showing `integrations.phone_sync_last` (e.g. "Last sync: 2026-07-24 15:04 — Imported 2, toggles 1" or "Never"), persisted key `integrations.phone_sync_enabled` written in `_on_accept` exactly like `wrike_enabled`.
- Consumes: `labels.make_selectable`; the settings raw-dict conventions.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_settings_integrations_tab.py` (reuse its dialog fixture):

```python
def test_phone_sync_toggle_persists(...):
    # default unchecked; check it; _on_accept; reload settings from disk →
    # integrations.phone_sync_enabled is True


def test_phone_sync_status_renders_last_sync(...):
    # seed settings._raw["integrations"]["phone_sync_last"] =
    #   {"at": "2026-07-24T15:04:00+00:00", "ok": True, "summary": "Imported 2"}
    # build dialog → status label text contains "Imported 2"; label is selectable
```

Write in full from the fixture.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_settings_integrations_tab.py -v -k phone_sync`
Expected: FAIL (no attribute).

- [ ] **Step 3: Implement**

In `_build_integrations_tab`, after the Wrike rows: a spacer `form.addRow(QLabel(""))`, then the checkbox (checked from `integrations.phone_sync_enabled`, default False) and the status label built from `phone_sync_last` (format helper `_phone_sync_status_text(raw) -> str`, module-level, unit-testable). In `_on_accept`: `s._raw.setdefault("integrations", {})["phone_sync_enabled"] = self.phone_sync_enable_cb.isChecked()` alongside the Wrike keys.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_settings_integrations_tab.py -v` then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/settings_dialog.py tests/ui/test_settings_integrations_tab.py
git commit -m "feat(ui): phone-sync toggle and status in Settings"
```

---

### Task 6: Real-device verification (controller-run)

Not a subagent task — the controller runs it with Brian's phone connected:

- [ ] `uv run python scripts/mtp_smoke.py` — finds the phone, lists the three contract dirs, push/pull/delete probe timings printed.
- [ ] Full end-to-end: place a real `.m4a` + sidecar in the phone's `outbox` (via the smoke script or Explorer), launch the app with phone sync enabled, plug the phone, verify: toast appears, recording imports and processes, `library/` on the phone contains the mirror, outbox emptied.
- [ ] `MtpNotReady` UX: lock the phone → watcher surfaces the unlock hint exactly once.
- [ ] Full suite + `ruff check` delta clean; final whole-branch review + fix wave; finishing-a-development-branch.
