"""Pure helper that assembles WrikeSyncPlanner kwargs from a recording."""

from __future__ import annotations

from teams_transcriber.storage import (
    Recording, RecordingRepo, RecordingSource, RecordingStatus,
    Summary, SummaryRepo, TodoItem, build_database,
)
from teams_transcriber.storage.wrike import WrikeTaskRepo, WrikeTaskRow
from teams_transcriber.ui.app import _wrike_open_planner_kwargs


def _seed(tmp_path):
    db = build_database(tmp_path / "f.db"); db.initialize()
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
        my_todos=[TodoItem(task="a")], action_items_others=[],
        key_decisions=[], follow_ups=[], topics=[],
        generated_at="2026-06-09T10:00:00+00:00", model_used="m",
    ))
    return db, rec.id


def test_open_planner_kwargs_builds_correct_dialog_inputs(tmp_path) -> None:
    db, rid = _seed(tmp_path)
    kwargs = _wrike_open_planner_kwargs(
        db, rid,
        folders=[{"id": "F1", "title": "Proj"}],
        recent_folder_ids=["F1"],
        contacts=[],
        assignee_suggestions={},
    )
    assert [i.kind for i in kwargs["items"]] == ["summary", "my_todo"]
    assert kwargs["recent_folder_ids"] == ["F1"]
    assert kwargs["already_synced_keys"] == set()
    db.close()


def test_open_planner_kwargs_maps_legacy_db_kind_to_synckind(tmp_path) -> None:
    """A persisted Phase-11 row (kind='my') must appear in already_synced_keys
    as the SyncKind ('my_todo'), so the planner locks the matching row."""
    db, rid = _seed(tmp_path)
    WrikeTaskRepo(db).insert(WrikeTaskRow(
        id=None, recording_id=rid, kind="my", todo_index=0,
        wrike_task_id="OLD", wrike_folder_id="F", created_at="2026-06-08T00:00:00+00:00",
        last_synced_done=False,
    ))
    kwargs = _wrike_open_planner_kwargs(
        db, rid, folders=[{"id": "F1", "title": "Proj"}],
        recent_folder_ids=["F1"], contacts=[], assignee_suggestions={},
    )
    assert ("my_todo", 0) in kwargs["already_synced_keys"]
    db.close()
