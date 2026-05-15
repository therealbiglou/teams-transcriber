from datetime import UTC, datetime

import pytest

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import (
    ActionItemOther,
    Recording,
    RecordingSource,
    RecordingStatus,
    Summary,
    TodoItem,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.summaries import SummaryRepo


def _now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def recording_id(db: Database) -> int:
    rec = RecordingRepo(db).create(
        Recording(
            id=None,
            started_at=_now(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title="X",
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.SUMMARIZING,
            error_message=None,
        )
    )
    assert rec.id is not None
    return rec.id


def _sample_summary(recording_id: int) -> Summary:
    return Summary(
        recording_id=recording_id,
        title="Billing rewrite alignment",
        one_line="Aligned on billing rewrite.",
        summary="We discussed the billing rewrite and agreed on a July release.",
        key_decisions=["Billing rewrite scheduled for July release"],
        my_todos=[
            TodoItem(task="Write API stub spec", context="Discussed at ~12:30", due="2026-05-16"),
        ],
        action_items_others=[
            ActionItemOther(who="Sarah", task="Review billing migration doc", due=None),
        ],
        follow_ups=["Revisit pricing tiers after legal review"],
        topics=["billing", "roadmap"],
        generated_at=_now(),
        model_used="claude-sonnet-4-6",
    )


def test_upsert_creates_summary(db: Database, recording_id: int) -> None:
    repo = SummaryRepo(db)
    repo.upsert(_sample_summary(recording_id))
    got = repo.get(recording_id)
    assert got is not None
    assert got.one_line == "Aligned on billing rewrite."
    assert got.my_todos[0].task == "Write API stub spec"
    assert got.action_items_others[0].who == "Sarah"
    assert got.topics == ["billing", "roadmap"]


def test_upsert_replaces_existing(db: Database, recording_id: int) -> None:
    repo = SummaryRepo(db)
    first = _sample_summary(recording_id)
    repo.upsert(first)

    second = _sample_summary(recording_id)
    second.one_line = "Re-summarized."
    second.topics = ["billing"]
    repo.upsert(second)

    got = repo.get(recording_id)
    assert got is not None
    assert got.one_line == "Re-summarized."
    assert got.topics == ["billing"]


def test_get_returns_none_when_missing(db: Database) -> None:
    repo = SummaryRepo(db)
    assert repo.get(999) is None


def test_empty_lists_round_trip(db: Database, recording_id: int) -> None:
    repo = SummaryRepo(db)
    s = _sample_summary(recording_id)
    s.key_decisions = []
    s.my_todos = []
    s.action_items_others = []
    s.follow_ups = []
    s.topics = []
    repo.upsert(s)
    got = repo.get(recording_id)
    assert got is not None
    assert got.key_decisions == []
    assert got.my_todos == []
    assert got.topics == []


def test_title_round_trips(db: Database, recording_id: int) -> None:
    repo = SummaryRepo(db)
    s = _sample_summary(recording_id)
    s.title = "Q2 roadmap sync"
    repo.upsert(s)
    got = repo.get(recording_id)
    assert got is not None
    assert got.title == "Q2 roadmap sync"


def test_null_title_one_line_summary_round_trip(db: Database, recording_id: int) -> None:
    """Summary rows for failed-but-partial summaries may have NULL text fields."""
    repo = SummaryRepo(db)
    s = _sample_summary(recording_id)
    s.title = None
    s.one_line = None
    s.summary = None
    repo.upsert(s)
    got = repo.get(recording_id)
    assert got is not None
    assert got.title is None
    assert got.one_line is None
    assert got.summary is None


def test_model_used_and_generated_at_round_trip(db: Database, recording_id: int) -> None:
    repo = SummaryRepo(db)
    s = _sample_summary(recording_id)
    s.generated_at = "2026-05-14T12:00:00+00:00"
    s.model_used = "claude-opus-4-7"
    repo.upsert(s)
    got = repo.get(recording_id)
    assert got is not None
    assert got.generated_at == "2026-05-14T12:00:00+00:00"
    assert got.model_used == "claude-opus-4-7"


def test_upsert_replaces_list_not_append(db: Database, recording_id: int) -> None:
    """Replacing a summary with fewer items must shrink the list, not concatenate."""
    repo = SummaryRepo(db)
    first = _sample_summary(recording_id)
    first.my_todos = [
        TodoItem(task="A"),
        TodoItem(task="B"),
        TodoItem(task="C"),
    ]
    repo.upsert(first)

    second = _sample_summary(recording_id)
    second.my_todos = [TodoItem(task="X")]
    repo.upsert(second)

    got = repo.get(recording_id)
    assert got is not None
    assert len(got.my_todos) == 1
    assert got.my_todos[0].task == "X"


def test_delete_removes_summary(db: Database, recording_id: int) -> None:
    repo = SummaryRepo(db)
    repo.upsert(_sample_summary(recording_id))
    assert repo.get(recording_id) is not None
    repo.delete(recording_id)
    assert repo.get(recording_id) is None


def test_delete_is_idempotent(db: Database) -> None:
    repo = SummaryRepo(db)
    # No row exists; delete must not raise.
    repo.delete(999)


def test_load_tolerates_unknown_json_keys(db: Database, recording_id: int) -> None:
    """If a future Summarizer writes extra keys, deserialization should drop them, not crash."""
    import json
    repo = SummaryRepo(db)
    repo.upsert(_sample_summary(recording_id))
    # Sneak around the repo to write a my_todos entry with an unknown field.
    with db.connect() as conn:
        conn.execute(
            "UPDATE summaries SET my_todos_json = ? WHERE recording_id = ?",
            (json.dumps([{"task": "Hi", "due": None, "context": None, "priority": "high"}]),
             recording_id),
        )
        conn.commit()
    got = repo.get(recording_id)
    assert got is not None
    assert len(got.my_todos) == 1
    assert got.my_todos[0].task == "Hi"


def test_load_tolerates_malformed_json(db: Database, recording_id: int) -> None:
    """If a JSON blob is malformed or wrong-shape, the list is treated as empty."""
    repo = SummaryRepo(db)
    repo.upsert(_sample_summary(recording_id))
    with db.connect() as conn:
        # Wrong shape — dict instead of list.
        conn.execute(
            "UPDATE summaries SET my_todos_json = '{}' WHERE recording_id = ?",
            (recording_id,),
        )
        # Malformed JSON.
        conn.execute(
            "UPDATE summaries SET topics_json = 'not valid json' WHERE recording_id = ?",
            (recording_id,),
        )
        conn.commit()
    got = repo.get(recording_id)
    assert got is not None
    assert got.my_todos == []
    assert got.topics == []
