"""Convert a stored Recording + Summary into a list of SyncItem."""

from __future__ import annotations

from teams_transcriber.integrations.wrike_items import (
    SyncItem,
    recording_to_sync_items,
)
from teams_transcriber.storage import (
    ActionItemOther,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    Summary,
    SummaryRepo,
    TodoItem,
    build_database,
)


def _seed(tmp_path):
    db = build_database(tmp_path / "items.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00",
        ended_at="2026-06-09T11:00:00+00:00",
        source=RecordingSource.MANUAL,
        detected_title="t", display_title="Q3 sync",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="Q3 sync",
        one_line="x",
        summary="We aligned on Q3 priorities.",
        key_decisions=["Ship in July", "Hire 2 PMs"],
        my_todos=[
            TodoItem(task="Email Jennifer"),
            TodoItem(task="Order banner"),
        ],
        action_items_others=[
            ActionItemOther(who="Sarah Kim", task="Migration doc"),
            ActionItemOther(who="the eng lead", task="IAM cutover"),
        ],
        follow_ups=["Revisit pricing", "Schedule next sync"],
        topics=[],
        generated_at="2026-06-09T10:00:00+00:00",
        model_used="claude-sonnet-4-6",
    ))
    return db, rec.id


def test_items_order_is_stable_and_complete(tmp_path) -> None:
    db, rid = _seed(tmp_path)
    items = recording_to_sync_items(db, rid)

    kinds = [i.kind for i in items]
    assert kinds == [
        "summary", "decisions",
        "my_todo", "my_todo",
        "action_other", "action_other",
        "follow_up", "follow_up",
    ]
    assert items[0].text == "We aligned on Q3 priorities."
    assert "Ship in July" in items[1].text and "Hire 2 PMs" in items[1].text
    assert [i.text for i in items[2:4]] == ["Email Jennifer", "Order banner"]
    assert items[4].suggested_who == "Sarah Kim"
    assert items[5].suggested_who == "the eng lead"
    assert items[6].text == "Revisit pricing"
    assert items[7].text == "Schedule next sync"

    assert items[0].index == 0
    assert items[1].index == 0
    assert items[2].index == 0 and items[3].index == 1
    assert items[4].index == 0 and items[5].index == 1
    assert items[6].index == 0 and items[7].index == 1

    db.close()


def test_items_skips_missing_sections(tmp_path) -> None:
    """A meeting with only my_todos should produce only my_todo items."""
    db = build_database(tmp_path / "min.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="min",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="min", one_line=None, summary=None,
        my_todos=[TodoItem(task="Just one")],
        action_items_others=[], key_decisions=[], follow_ups=[], topics=[],
        generated_at="2026-06-09T10:00:00+00:00", model_used="m",
    ))
    items = recording_to_sync_items(db, rec.id)
    assert [i.kind for i in items] == ["my_todo"]
    db.close()


def test_returns_empty_for_unknown_recording(tmp_path) -> None:
    db = build_database(tmp_path / "empty.db")
    db.initialize()
    assert recording_to_sync_items(db, 9999) == []
    db.close()
