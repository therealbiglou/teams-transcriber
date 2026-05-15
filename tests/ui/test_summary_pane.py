from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from teams_transcriber.storage import (
    ActionItemOther,
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
from teams_transcriber.ui.summary_pane import SummaryPane


@pytest.fixture
def db_with_summary(tmp_path: Path):
    db = build_database(tmp_path / "tt.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-15T10:00:00+00:00",
        ended_at="2026-05-15T10:30:00+00:00",
        source=RecordingSource.TEAMS, detected_title="X | Microsoft Teams",
        display_title="Q2 sync", audio_path=None, audio_deleted_at=None,
        duration_ms=30 * 60 * 1000, status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id,
        title="Q2 sync",
        one_line="Aligned on x.",
        summary="Discussed x.",
        key_decisions=["Ship in July"],
        my_todos=[TodoItem(task="Do A"), TodoItem(task="Do B")],
        action_items_others=[ActionItemOther(who="Sarah", task="Migration doc")],
        follow_ups=["Revisit pricing"],
        topics=["billing"],
        generated_at=datetime.now(UTC).isoformat(),
        model_used="claude-sonnet-4-6",
    ))
    yield db, rec.id
    db.close()


def test_pane_shows_summary(qapp, qtbot, db_with_summary) -> None:
    db, rec_id = db_with_summary
    pane = SummaryPane(db)
    pane.show_recording(rec_id)
    from PySide6.QtWidgets import QCheckBox
    todos = pane.findChildren(QCheckBox)
    assert len(todos) == 2
    assert "Do A" in todos[0].text()


def test_todo_toggle_persists(qapp, qtbot, db_with_summary) -> None:
    db, rec_id = db_with_summary
    pane = SummaryPane(db)
    pane.show_recording(rec_id)
    from PySide6.QtWidgets import QCheckBox
    cbs = pane.findChildren(QCheckBox)
    cbs[0].setChecked(True)
    states = TodoStateRepo(db).list_for_recording(rec_id)
    assert any(s.done and s.task_text == "Do A" for s in states)
