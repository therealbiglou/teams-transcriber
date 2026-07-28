"""App wiring around the notes window: no-fallback-to-most-recent toast
(Fix 3) and the ``_notes_windows`` registry (Fix 7). Mirrors the
``App.__new__`` + ``SimpleNamespace`` pattern used elsewhere in tests/ui/ --
no full ``App()`` construction (tray/pipeline/first-run wizard)."""

from __future__ import annotations

from types import SimpleNamespace


def test_no_active_recording_toasts_instead_of_opening_most_recent(qapp, monkeypatch, tmp_path):
    """Opening notes with nothing recording must not fall back to the most
    recent (already summarized/exported) recording -- that would present a
    dead Stop button and notes that never reach Wrike. It should surface the
    existing in-app toast instead."""
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        build_database,
    )
    from teams_transcriber.ui.app import App

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    RecordingRepo(db).create(Recording(
        id=None, started_at="2026-07-26T15:00:00+00:00", ended_at="2026-07-26T15:30:00+00:00",
        source=RecordingSource.MANUAL, detected_title=None, display_title="Q3 Sync",
        audio_path=None, audio_deleted_at=None, duration_ms=1_800_000,
        status=RecordingStatus.DONE, error_message=None,
    ))

    app = App.__new__(App)
    app.db = db
    app._active_recording_id = None
    app._notes_windows = {}

    toasts: list[tuple] = []
    monkeypatch.setattr(
        "teams_transcriber.ui.app.show_in_app_toast",
        lambda *a, **kw: toasts.append(a),
    )

    try:
        app._open_notes_window()
    finally:
        db.close()

    assert len(toasts) == 1
    assert toasts[0][0] == "Nothing to show yet"
    assert app._notes_windows == {}  # no window was opened


def test_notes_windows_registry_initialized_in_init():
    """``self._notes_windows`` must be created once in ``App.__init__`` (not
    lazily via ``getattr(self, "_notes_windows", {})`` in three separate
    call sites, which risks a throwaway dict silently dropping an existing
    window reference)."""
    import inspect

    from teams_transcriber.ui.app import App

    src = inspect.getsource(App.__init__)
    assert "self._notes_windows" in src
    assert "getattr(self, \"_notes_windows\"" not in src


def test_open_notes_window_does_not_use_getattr_fallback():
    import inspect

    from teams_transcriber.ui.app import App

    src = inspect.getsource(App._open_notes_window)
    assert "getattr(self, \"_notes_windows\"" not in src


def test_notes_window_closed_does_not_use_getattr_fallback():
    import inspect

    from teams_transcriber.ui.app import App

    src = inspect.getsource(App._on_notes_window_closed)
    assert "getattr(self, \"_notes_windows\"" not in src
