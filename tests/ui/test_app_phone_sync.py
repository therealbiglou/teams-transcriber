"""App-level phone-sync wiring: mirrors test_app_wrike_close_loop.py's
App.__new__ + SimpleNamespace pattern (no full App() construction)."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QWidget


def test_on_phone_todos_changed_refreshes_history_and_master(qapp):
    """_on_phone_todos_changed(rid) must run the same duo as
    _on_todo_state_changed: history refresh, master-view reload -- this is
    the ledgered Phase-1 gap (CLI passed None). The Wrike close-loop was
    dropped in Task 7 in favor of auto project export on SummaryReady, so
    it must NOT be called here anymore."""
    from teams_transcriber.ui.app import App

    app = App.__new__(App)

    refresh_calls: list[str | None] = []
    app._refresh_history = lambda query=None: refresh_calls.append(query)
    app.search = SimpleNamespace(input=SimpleNamespace(text=lambda: ""))

    reload_calls: list[int] = []
    app.master_todos = SimpleNamespace(reload=lambda: reload_calls.append(1))

    wrike_calls: list[int] = []
    app._wrike_close_loop_sync = wrike_calls.append

    app._on_phone_todos_changed(42)

    assert refresh_calls == [None]
    assert reload_calls == [1]
    assert wrike_calls == []


def test_phone_sync_cycle_persists_status_and_toasts(qapp, qtbot, tmp_path, monkeypatch):
    """_phone_sync_cycle builds a fresh MtpTransport(find_phone_root()), runs
    run_sync, persists a phone_sync_last status into settings, and toasts the
    CLI-style summary line via the main-thread singleShot hop."""
    from teams_transcriber.config import Settings
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.phone_sync.sync import PhoneSyncReport
    from teams_transcriber.ui import app as app_mod
    from teams_transcriber.ui.app import App

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()

    app = App.__new__(App)
    app.paths = paths
    app.settings = Settings()
    app.db = object()  # opaque -- run_sync is monkeypatched below
    app.pipeline = SimpleNamespace(import_phone_recording=lambda *a, **k: None)
    app.window = QWidget()  # QTimer.singleShot's 3-arg form needs a real QObject
    app._on_phone_todos_changed = lambda rid: None

    fake_root = object()
    monkeypatch.setattr(app_mod, "find_phone_root", lambda: fake_root)

    created: list[object] = []
    closed: list[bool] = []

    class FakeTransport:
        def __init__(self, root):
            created.append(root)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(app_mod, "MtpTransport", FakeTransport)

    canned = PhoneSyncReport(
        imported=[("uid1", 1)], skipped_known=2, toggles_applied=3,
        toggles_skipped_stale=1, failures=[],
    )
    run_sync_calls: list[tuple] = []

    def fake_run_sync(db, transport, *, import_recording, on_todos_changed, now_iso):
        run_sync_calls.append((db, transport, import_recording, on_todos_changed, now_iso))
        return canned

    monkeypatch.setattr(app_mod, "run_sync", fake_run_sync)

    toasts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_mod, "show_in_app_toast",
        lambda title, body, **kw: toasts.append((title, body)),
    )

    app._phone_sync_cycle()

    # run_sync got a freshly-built transport wrapping the freshly-probed root.
    assert created == [fake_root]
    assert len(run_sync_calls) == 1
    _db, transport_arg, import_recording, on_todos_changed, _now_iso = run_sync_calls[0]
    assert isinstance(transport_arg, FakeTransport)
    assert import_recording is app.pipeline.import_phone_recording
    assert closed == [True]

    # on_todos_changed must never touch Qt objects on the calling (watcher)
    # thread -- it has to hop to the main thread before reaching
    # _on_phone_todos_changed, same as the toast below.
    todo_calls: list[int] = []
    app._on_phone_todos_changed = todo_calls.append
    on_todos_changed(7)
    qtbot.waitUntil(lambda: todo_calls == [7], timeout=2000)

    # phone_sync_last mutation + save_settings() are hopped to the main
    # thread together with the toast (same singleShot callable) -- assert
    # via waitUntil rather than expecting them synchronously.
    qtbot.waitUntil(
        lambda: "phone_sync_last" in app.settings._raw.get("integrations", {}),
        timeout=2000,
    )
    last = app.settings._raw["integrations"]["phone_sync_last"]
    assert last["ok"] is True
    assert "Imported 1" in last["summary"]

    # Persisted to disk too, not just in memory.
    from teams_transcriber.config import load_settings

    def _persisted() -> bool:
        reloaded = load_settings(paths)
        return reloaded._raw.get("integrations", {}).get("phone_sync_last", {}).get("ok") is True

    qtbot.waitUntil(_persisted, timeout=2000)

    # Toast is hopped to the main thread via QTimer.singleShot -- assert
    # via waitUntil rather than expecting it synchronously.
    qtbot.waitUntil(lambda: len(toasts) == 1, timeout=2000)
    assert toasts[0][0] == "Phone sync"
    assert "Imported 1" in toasts[0][1]
    assert "failures 0" in toasts[0][1]


def test_phone_sync_cycle_marks_failed_report_not_ok(qapp, qtbot, tmp_path, monkeypatch):
    """A report with failures persists ok=False (not just 'no crash')."""
    from teams_transcriber.config import Settings
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.phone_sync.sync import PhoneSyncReport
    from teams_transcriber.ui import app as app_mod
    from teams_transcriber.ui.app import App

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()

    app = App.__new__(App)
    app.paths = paths
    app.settings = Settings()
    app.db = object()
    app.pipeline = SimpleNamespace(import_phone_recording=lambda *a, **k: None)
    app.window = QWidget()  # QTimer.singleShot's 3-arg form needs a real QObject
    app._on_phone_todos_changed = lambda rid: None

    monkeypatch.setattr(app_mod, "find_phone_root", lambda: object())

    class FakeTransport:
        def __init__(self, root):
            pass

        def close(self):
            pass

    monkeypatch.setattr(app_mod, "MtpTransport", FakeTransport)

    failing = PhoneSyncReport(failures=[("rec_x.m4a", "size mismatch")])
    monkeypatch.setattr(
        app_mod, "run_sync",
        lambda *a, **k: failing,
    )
    toasts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_mod, "show_in_app_toast",
        lambda title, body, **kw: toasts.append((title, body)),
    )

    app._phone_sync_cycle()

    qtbot.waitUntil(
        lambda: "phone_sync_last" in app.settings._raw.get("integrations", {}),
        timeout=2000,
    )
    last = app.settings._raw["integrations"]["phone_sync_last"]
    assert last["ok"] is False
    qtbot.waitUntil(lambda: len(toasts) == 1, timeout=2000)
    assert "failures 1" in toasts[0][1]


def test_apply_phone_sync_setting_starts_and_stops_watcher(qapp, tmp_path):
    """Watcher runs only when integrations.phone_sync_enabled is true."""
    from teams_transcriber.config import Settings
    from teams_transcriber.ui.app import App

    app = App.__new__(App)
    app.settings = Settings()
    app.settings._raw.setdefault("integrations", {})["phone_sync_enabled"] = False
    app._phone_watcher = None

    app._apply_phone_sync_setting()
    assert app._phone_watcher is None

    app.settings._raw["integrations"]["phone_sync_enabled"] = True
    app._apply_phone_sync_setting()
    assert app._phone_watcher is not None
    watcher = app._phone_watcher

    app.settings._raw["integrations"]["phone_sync_enabled"] = False
    app._apply_phone_sync_setting()
    assert app._phone_watcher is None
    assert watcher._thread is None  # stop() joined and cleared the thread


def test_apply_phone_sync_setting_enabled_twice_is_idempotent(qapp):
    """Two consecutive enabled=True applies (startup + a Settings save that
    didn't touch the flag) must keep the SAME watcher instance and exactly
    one live watcher thread; a subsequent disable stops and joins it."""
    import threading

    from teams_transcriber.config import Settings
    from teams_transcriber.ui.app import App

    app = App.__new__(App)
    app.settings = Settings()
    app.settings._raw.setdefault("integrations", {})["phone_sync_enabled"] = True
    app._phone_watcher = None

    app._apply_phone_sync_setting()
    first = app._phone_watcher
    assert first is not None
    first_thread = first._thread
    assert first_thread is not None and first_thread.is_alive()

    app._apply_phone_sync_setting()  # e.g. Settings saved with flag unchanged
    assert app._phone_watcher is first          # same instance, not respawned
    assert first._thread is first_thread        # same thread, no second spawn
    assert (
        sum(
            1 for t in threading.enumerate()
            if t.name == "PhoneSyncWatcher" and t.is_alive()
        )
        == 1
    )

    app.settings._raw["integrations"]["phone_sync_enabled"] = False
    app._apply_phone_sync_setting()
    assert app._phone_watcher is None
    assert not first_thread.is_alive()          # stop() joined it


def test_quit_stops_phone_watcher_before_teardown(qapp):
    """_quit must stop a running phone watcher first, ahead of hotkeys/
    pipeline/db teardown -- otherwise the watcher thread can still be
    mid-cycle (touching self.db / self.pipeline) after they're torn down."""
    from teams_transcriber.ui.app import App

    app = App.__new__(App)
    order: list[str] = []
    app._phone_watcher = SimpleNamespace(stop=lambda: order.append("watcher_stop"))
    app.hotkeys = SimpleNamespace(stop=lambda: order.append("hotkeys_stop"))
    app.pipeline = SimpleNamespace(shutdown=lambda: order.append("pipeline_shutdown"))
    app.db = SimpleNamespace(close=lambda: order.append("db_close"))
    app.qapp = SimpleNamespace(quit=lambda: order.append("qapp_quit"))

    app._quit()

    assert order == ["watcher_stop", "hotkeys_stop", "pipeline_shutdown", "db_close", "qapp_quit"]


def test_quit_with_no_phone_watcher_does_not_crash(qapp):
    """No watcher running (phone sync never enabled) -- _quit must tolerate None."""
    from teams_transcriber.ui.app import App

    app = App.__new__(App)
    app._phone_watcher = None
    app.hotkeys = SimpleNamespace(stop=lambda: None)
    app.pipeline = SimpleNamespace(shutdown=lambda: None)
    app.db = SimpleNamespace(close=lambda: None)
    app.qapp = SimpleNamespace(quit=lambda: None)

    app._quit()  # must not raise


def test_quit_for_update_stops_phone_watcher_before_teardown(qapp):
    """_quit_for_update (pre-installer shutdown path) must stop the phone
    watcher first too, same as _quit."""
    from teams_transcriber.ui.app import App

    app = App.__new__(App)
    order: list[str] = []
    app._phone_watcher = SimpleNamespace(stop=lambda: order.append("watcher_stop"))
    app.hotkeys = SimpleNamespace(stop=lambda: order.append("hotkeys_stop"))
    app.pipeline = SimpleNamespace(shutdown=lambda: order.append("pipeline_shutdown"))
    app.db = SimpleNamespace(close=lambda: order.append("db_close"))
    app.qapp = SimpleNamespace(exit=lambda code: order.append(("qapp_exit", code)))

    app._quit_for_update()

    assert order == [
        "watcher_stop", "hotkeys_stop", "pipeline_shutdown", "db_close", ("qapp_exit", 0),
    ]


def test_open_settings_tab_reconnects_phone_sync_setting_and_history_refresh():
    """Wiring regression guard: the dlg.saved connection point must keep
    refreshing history AND re-apply the phone-sync enabled/disabled toggle
    after Settings closes -- both are settings.json-driven state."""
    import inspect

    from teams_transcriber.ui.app import App

    source = inspect.getsource(App._open_settings_tab)
    assert "dlg.saved.connect(self._refresh_history)" in source
    assert "dlg.saved.connect(self._apply_phone_sync_setting)" in source
