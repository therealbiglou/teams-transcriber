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
