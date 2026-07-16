"""_refresh_history's done-count must ignore stale todo_state rows.

todo_state keeps rows for indices beyond a shrunk my_todos (seed never
prunes on re-summarization); the history chip's done count must be bound
to the CURRENT summary's todo count, mirroring the fix already applied to
phone_sync/library_export.py's todos_done.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace


def test_refresh_history_ignores_stale_todo_state_rows(tmp_path):
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        Summary,
        SummaryRepo,
        TodoItem,
        TodoStateRepo,
        build_database,
    )
    from teams_transcriber.ui.app import App
    from teams_transcriber.ui.sidebar import SidebarBucket

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    try:
        rec = RecordingRepo(db).create(Recording(
            id=None, started_at="2026-07-14T09:00:00+00:00", ended_at=None,
            source=RecordingSource.MANUAL, detected_title="t", display_title="t",
            audio_path=None, audio_deleted_at=None, duration_ms=1000,
            status=RecordingStatus.DONE, error_message=None,
        ))
        assert rec.id is not None
        SummaryRepo(db).upsert(Summary(
            recording_id=rec.id, title="t", one_line="x", summary="x",
            key_decisions=[],
            my_todos=[TodoItem(task="Do A"), TodoItem(task="Do B")],
            action_items_others=[], follow_ups=[], topics=[],
            generated_at=datetime.now(UTC).isoformat(), model_used="claude-sonnet-4-6",
        ))
        todo_repo = TodoStateRepo(db)
        # Current todo (index 0) marked done.
        todo_repo.mark_done(rec.id, 0, True, task_text="Do A")
        # Stale todo_state row (index 5) left over from a shrunk my_todos --
        # must NOT be counted.
        todo_repo.mark_done(rec.id, 5, True, task_text="stale")

        app = App.__new__(App)
        app.db = db
        app.window = SimpleNamespace(
            sidebar=SimpleNamespace(active_bucket=SidebarBucket.ALL)
        )
        captured: list[list[tuple]] = []
        app.history = SimpleNamespace(set_recordings=captured.append)

        app._refresh_history()

        assert len(captured) == 1
        rows = captured[0]
        assert len(rows) == 1
        _rec, _one_line, todos, done = rows[0]
        assert todos == 2
        assert done == 1
    finally:
        db.close()
