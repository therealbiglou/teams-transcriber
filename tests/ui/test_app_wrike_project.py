"""App wiring for Wrike project export (Task 7): auto-push on SummaryReady,
manual re-send, and the assignee-resolve helper. Mirrors
test_app_wrike_close_loop.py's App.__new__ + SimpleNamespace pattern -- no
real network, no full App() construction."""

from __future__ import annotations

from types import SimpleNamespace


def _app_with_integrations(integrations: dict) -> "App":  # noqa: F821
    from teams_transcriber.ui.app import App

    app = App.__new__(App)
    app.settings = SimpleNamespace(_raw={"integrations": integrations})
    return app


def test_summary_ready_spawns_export_when_enabled(qapp, monkeypatch):
    app = _app_with_integrations(
        {"wrike_project_export_enabled": True, "wrike_parent_id": "P1"}
    )
    monkeypatch.setattr("keyring.get_password", lambda *a, **kw: "tok")

    calls: list[int] = []
    app._wrike_export_worker = calls.append  # type: ignore[assignment]

    app._on_summary_ready_wrike(SimpleNamespace(recording_id=5))

    assert calls == [5]


def test_summary_ready_noop_when_disabled(qapp, monkeypatch):
    app = _app_with_integrations(
        {"wrike_project_export_enabled": False, "wrike_parent_id": "P1"}
    )
    monkeypatch.setattr("keyring.get_password", lambda *a, **kw: "tok")

    calls: list[int] = []
    app._wrike_export_worker = calls.append  # type: ignore[assignment]

    app._on_summary_ready_wrike(SimpleNamespace(recording_id=5))

    assert calls == []


def test_wrike_project_enabled_requires_token_toggle_and_parent(qapp, monkeypatch):
    cases = [
        # (has_token, toggle, parent_id) -> expected
        (True, True, "P1", True),
        (False, True, "P1", False),   # no token
        (True, False, "P1", False),   # toggle off
        (True, True, None, False),    # no destination chosen
        (True, True, "", False),      # empty parent id also counts as unset
        (False, False, None, False),
    ]
    for has_token, toggle, parent_id, expected in cases:
        app = _app_with_integrations(
            {"wrike_project_export_enabled": toggle, "wrike_parent_id": parent_id}
        )
        monkeypatch.setattr(
            "keyring.get_password", lambda *a, **kw: ("tok" if has_token else "")
        )
        assert app._wrike_project_enabled() is expected, (has_token, toggle, parent_id)


def _seed_summary(tmp_path, *, action_items_others):
    from teams_transcriber.storage import (
        Recording, RecordingRepo, RecordingSource, RecordingStatus,
        Summary, SummaryRepo, TodoItem, build_database,
    )

    db = build_database(tmp_path / "f.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="m",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="m", one_line=None, summary="body",
        my_todos=[TodoItem(task="a")], action_items_others=action_items_others,
        key_decisions=[], follow_ups=[], topics=[],
        generated_at="2026-06-09T10:00:00+00:00", model_used="m",
    ))
    return db, rec.id


def test_resolve_wrike_assignees_empty_when_no_action_items_others(qapp, tmp_path):
    from teams_transcriber.ui.app import App

    db, rid = _seed_summary(tmp_path, action_items_others=[])
    try:
        app = App.__new__(App)
        app.db = db
        app.settings = SimpleNamespace(
            _raw={"integrations": {"wrike_llm_assignee_fallback": True}},
            ai_model="claude-x",
        )
        app._anthropic_key = lambda: "k"

        class _BoomClient:
            def list_contacts(self):
                raise AssertionError("list_contacts should not be called")

        result = app._resolve_wrike_assignees(rid, _BoomClient())
        assert result == {}
    finally:
        db.close()


def test_resolve_wrike_assignees_calls_suggest_assignees(qapp, tmp_path, monkeypatch):
    from teams_transcriber.storage.models import ActionItemOther
    from teams_transcriber.ui.app import App

    db, rid = _seed_summary(
        tmp_path, action_items_others=[ActionItemOther(who="Alex", task="ship it")]
    )
    try:
        app = App.__new__(App)
        app.db = db
        app.settings = SimpleNamespace(
            _raw={"integrations": {"wrike_llm_assignee_fallback": True}},
            ai_model="claude-x",
        )
        app._anthropic_key = lambda: "k"

        class _Client:
            def list_contacts(self):
                return [{"id": "C1", "firstName": "Alex", "lastName": "Doe"}]

        captured = {}

        def _fake_suggest_assignees(items, contacts, *, meeting_summary, api_key, model, llm_fallback):
            captured["items"] = items
            captured["contacts"] = contacts
            captured["meeting_summary"] = meeting_summary
            captured["api_key"] = api_key
            captured["model"] = model
            captured["llm_fallback"] = llm_fallback
            return {0: "C1"}

        monkeypatch.setattr(
            "teams_transcriber.integrations.wrike_assignees.suggest_assignees",
            _fake_suggest_assignees,
        )

        result = app._resolve_wrike_assignees(rid, _Client())

        assert result == {0: "C1"}
        assert captured["items"] == [(0, "Alex")]
        assert captured["api_key"] == "k"
        assert captured["model"] == "claude-x"
        assert captured["llm_fallback"] is True
    finally:
        db.close()


def test_resolve_wrike_assignees_llm_fallback_off_without_key(qapp, tmp_path, monkeypatch):
    """No Anthropic key -> suggest_assignees must be called with
    llm_fallback=False (fuzzy-only), even when the toggle is on."""
    from teams_transcriber.storage.models import ActionItemOther
    from teams_transcriber.ui.app import App

    db, rid = _seed_summary(
        tmp_path, action_items_others=[ActionItemOther(who="Alex", task="ship it")]
    )
    try:
        app = App.__new__(App)
        app.db = db
        app.settings = SimpleNamespace(
            _raw={"integrations": {"wrike_llm_assignee_fallback": True}},
            ai_model="claude-x",
        )
        app._anthropic_key = lambda: ""   # no key

        class _Client:
            def list_contacts(self):
                return [{"id": "C1", "firstName": "Alex", "lastName": "Doe"}]

        captured = {}

        def _fake_suggest_assignees(items, contacts, *, meeting_summary, api_key, model, llm_fallback):
            captured["llm_fallback"] = llm_fallback
            return {}

        monkeypatch.setattr(
            "teams_transcriber.integrations.wrike_assignees.suggest_assignees",
            _fake_suggest_assignees,
        )

        app._resolve_wrike_assignees(rid, _Client())

        assert captured["llm_fallback"] is False
    finally:
        db.close()


def test_wrike_export_worker_skips_when_already_in_flight(qapp, monkeypatch):
    """Two entry points racing for the same recording_id (e.g. the
    SummaryReady auto-push firing while the manual Send button is also
    clicked) must not both pass the guard -- only the first spawns a worker;
    the second toasts and returns."""
    from teams_transcriber.ui.app import App

    app = App.__new__(App)
    app._wrike_exports_in_flight = {5}

    def _boom(*a, **kw):
        raise AssertionError("must not spawn a second worker for an in-flight recording_id")

    monkeypatch.setattr("threading.Thread", _boom)

    toasts: list[tuple] = []
    monkeypatch.setattr(
        "teams_transcriber.ui.app.show_in_app_toast",
        lambda *a, **kw: toasts.append(a),
    )

    app._wrike_export_worker(5)

    assert len(toasts) == 1
    assert app._wrike_exports_in_flight == {5}  # unchanged -- no duplicate add


def test_wrike_export_worker_adds_to_in_flight_and_release_hop_discards(qapp, monkeypatch, qtbot):
    """A normal (non-racing) call adds recording_id to the in-flight set
    synchronously (on the main thread, before the worker thread spawns), and
    the worker's finally-hop discards it back on the main thread once the
    background worker finishes."""
    from PySide6.QtWidgets import QWidget

    from teams_transcriber.ui.app import App

    app = App.__new__(App)
    app._wrike_exports_in_flight = set()
    # No token/parent_id configured -- the worker thread returns immediately
    # after the keyring/settings check, so the finally-hop fires promptly.
    app.settings = SimpleNamespace(_raw={"integrations": {}})
    app.window = QWidget()  # QTimer.singleShot's 3-arg form needs a real QObject
    monkeypatch.setattr("keyring.get_password", lambda *a, **kw: "")

    app._wrike_export_worker(5)

    assert app._wrike_exports_in_flight == {5}

    qtbot.waitUntil(lambda: app._wrike_exports_in_flight == set(), timeout=2000)


def test_wrike_export_worker_refreshes_history_on_every_exit_path(qapp, monkeypatch, qtbot):
    """A successful (or failed, or not-configured) export must refresh the
    history list on the main thread -- otherwise a row stays stuck reading
    "Not in Wrike" / "Send to Wrike" after the project already exists, and
    re-clicking re-runs a paid Anthropic call for an already-idempotent push.
    Exercised via the same not-configured short-circuit as the test above
    (fastest path through the worker) so this only needs keyring + settings,
    no real Wrike client."""
    from PySide6.QtWidgets import QWidget

    from teams_transcriber.ui.app import App

    app = App.__new__(App)
    app._wrike_exports_in_flight = set()
    app.settings = SimpleNamespace(_raw={"integrations": {}})
    app.window = QWidget()
    monkeypatch.setattr("keyring.get_password", lambda *a, **kw: "")

    refresh_calls: list[bool] = []
    app._refresh_history = lambda: refresh_calls.append(True)

    app._wrike_export_worker(5)

    qtbot.waitUntil(lambda: app._wrike_exports_in_flight == set(), timeout=2000)
    assert refresh_calls == [True]
