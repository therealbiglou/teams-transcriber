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


def test_on_todo_state_changed_refreshes_without_close_loop(qapp):
    """_on_todo_state_changed refreshes history + reloads the master view but
    -- since Task 7 dropped the ledgered close-loop -- must NOT call the
    (now-unreferenced) close-loop sync. Mirrors the _on_master_todo_toggled
    test in test_app_wrike_close_loop.py."""
    from teams_transcriber.ui.app import App

    app = App.__new__(App)
    app.search = SimpleNamespace(input=SimpleNamespace(text=lambda: ""))

    refresh_calls: list[int] = []
    app._refresh_history = lambda query=None: refresh_calls.append(1)
    reload_calls: list[int] = []
    app.master_todos = SimpleNamespace(reload=lambda: reload_calls.append(1))
    close_loop_calls: list[int] = []
    app._wrike_close_loop_sync = close_loop_calls.append  # type: ignore[assignment]

    app._on_todo_state_changed(7)

    assert refresh_calls == [1]
    assert reload_calls == [1]
    assert close_loop_calls == []
