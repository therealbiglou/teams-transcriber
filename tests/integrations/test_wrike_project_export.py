"""Tests for the create-once Wrike project export orchestrator.

Seeding mirrors tests/ui/test_summary_pane.py: build_database (full
migration set) + RecordingRepo/SummaryRepo/TranscriptRepo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.integrations.wrike_project_export import export_recording
from teams_transcriber.storage import (
    ActionItemOther,
    Channel,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    Summary,
    SummaryRepo,
    TodoItem,
    TranscriptRepo,
    TranscriptSegment,
    build_database,
)
from teams_transcriber.storage.wrike import WrikeProjectRepo, WrikeTaskRepo


class FakeClient:
    """Records every call it receives; returns canned incrementing ids."""

    def __init__(self, *, fail_first_create_task: bool = False) -> None:
        self.fail_first_create_task = fail_first_create_task
        self._create_task_calls = 0
        self._counters = {"prj": 0, "att": 0, "tsk": 0, "cmt": 0}

        self.create_project_calls: list[tuple[str, str, str]] = []
        self.update_project_calls: list[tuple[str, str]] = []
        self.upload_attachment_calls: list[tuple[str, str, bytes]] = []
        self.delete_attachment_calls: list[str] = []
        self.create_task_calls: list[tuple[str, dict]] = []
        self.create_comment_calls: list[tuple[str, str, str]] = []

    def _next(self, prefix: str) -> str:
        self._counters[prefix] += 1
        return f"{prefix}{self._counters[prefix]}"

    def create_project(self, parent_id, title, description):
        pid = self._next("prj")
        self.create_project_calls.append((parent_id, title, description))
        return {"id": pid, "permalink": f"https://w/open.htm?id={pid[3:]}"}

    def update_project(self, project_id, *, description):
        self.update_project_calls.append((project_id, description))
        return {}

    def upload_attachment(self, entity_id, filename, content):
        aid = self._next("att")
        self.upload_attachment_calls.append((entity_id, filename, content))
        return aid

    def delete_attachment(self, attachment_id):
        self.delete_attachment_calls.append(attachment_id)

    def create_task(self, folder_id, payload):
        self._create_task_calls += 1
        if self.fail_first_create_task and self._create_task_calls == 1:
            raise RuntimeError("wrike create_task boom")
        tid = self._next("tsk")
        self.create_task_calls.append((folder_id, payload))
        return {"id": tid}

    def create_comment(self, *, entity_type, entity_id, text):
        cid = self._next("cmt")
        self.create_comment_calls.append((entity_type, entity_id, text))
        return cid


def _make_db(tmp_path: Path):
    db = build_database(tmp_path / "tt.db")
    db.initialize()
    return db


def _seed_recording(
    db, *, my_todos, action_items_others, follow_ups, manual_notes=None,
) -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-07-01T10:00:00+00:00",
        ended_at="2026-07-01T10:30:00+00:00",
        source=RecordingSource.TEAMS, detected_title="X | Microsoft Teams",
        display_title="Q3 sync", audio_path=None, audio_deleted_at=None,
        duration_ms=30 * 60 * 1000, status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    if manual_notes is not None:
        RecordingRepo(db).set_manual_notes(rec.id, manual_notes)
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="Q3 sync", one_line="Aligned on x.",
        summary="Discussed x.", key_decisions=["Ship in July"],
        my_todos=my_todos, action_items_others=action_items_others,
        follow_ups=follow_ups, topics=["billing"],
        generated_at="2026-07-01T10:30:00+00:00", model_used="claude-sonnet-4-6",
    ))
    TranscriptRepo(db).append_many([
        TranscriptSegment(
            id=None, recording_id=rec.id, start_ms=0, end_ms=5000,
            channel=Channel.ME, text="Hello from the meeting.",
        ),
    ])
    return rec.id


@pytest.fixture
def db_seeded(tmp_path):
    db = _make_db(tmp_path)
    yield db
    db.close()


def test_first_export_creates_project_tasks_attachment_and_notes(db_seeded):
    db = db_seeded
    rid = _seed_recording(
        db,
        my_todos=[TodoItem(task="Do A"), TodoItem(task="Do B")],
        action_items_others=[ActionItemOther(who="Sam", task="Send doc")],
        follow_ups=["Revisit pricing"],
        manual_notes="<p>call Bob</p>",
    )
    fake = FakeClient()

    report = export_recording(db, fake, rid, parent_id="P", assignees={0: "c-sam"})

    assert report.project_id == "prj1"
    assert report.permalink == "https://w/open.htm?id=1"
    assert report.created_tasks == 4
    assert report.updated is False
    assert report.failures == []

    assert len(fake.create_project_calls) == 1
    parent_id, _title, description = fake.create_project_calls[0]
    assert parent_id == "P"
    assert "Discussed x." in description
    assert "Ship in July" in description

    assert len(fake.upload_attachment_calls) == 1
    entity, filename, content = fake.upload_attachment_calls[0]
    assert entity == "prj1"
    assert filename.endswith(".md")
    assert "# Transcript" in content.decode("utf-8")

    other_task_payloads = [
        payload for _folder_id, payload in fake.create_task_calls
        if payload.get("responsibles") == ["c-sam"]
    ]
    assert len(other_task_payloads) == 1
    assert "Sam" in other_task_payloads[0]["title"]

    assert len(fake.create_comment_calls) == 1
    entity_type, entity_id, text = fake.create_comment_calls[0]
    assert entity_type == "folder"
    assert entity_id == "prj1"
    assert "call Bob" in text
    assert "<p>" not in text

    proj_row = WrikeProjectRepo(db).get(rid)
    assert proj_row is not None
    assert proj_row.project_id == "prj1"
    assert proj_row.attachment_id == "att1"
    assert proj_row.notes_comment_id == "cmt1"

    tasks = WrikeTaskRepo(db).list_for_recording(rid)
    assert len(tasks) == 4
    kinds = {(t.kind, t.todo_index) for t in tasks}
    assert kinds == {("my", 0), ("my", 1), ("other", 0), ("follow_up", 0)}


def test_due_dates_become_planned_wrike_dates_not_title_text(db_seeded):
    db = db_seeded
    rid = _seed_recording(
        db,
        my_todos=[
            TodoItem(task="Write shot list", due="2026-07-28"),
            TodoItem(task="Book studio"),  # no due
            TodoItem(task="Odd date", due="next week"),  # unparseable → dropped
        ],
        action_items_others=[ActionItemOther(who="Priya", task="Style frames", due="2026-07-30")],
        follow_ups=[],
    )
    fake = FakeClient()

    report = export_recording(db, fake, rid, parent_id="P", assignees={})

    assert report.failures == []
    payload_by_title = {p["title"]: p for _folder, p in fake.create_task_calls}

    # due date → a single-day Planned task, and the title stays clean (no "(due …)")
    assert payload_by_title["Write shot list"]["dates"] == {
        "type": "Planned", "start": "2026-07-28", "due": "2026-07-28",
    }
    # action-item due dates are honored too (previously ignored entirely)
    assert payload_by_title["Priya: Style frames"]["dates"] == {
        "type": "Planned", "start": "2026-07-30", "due": "2026-07-30",
    }
    # no due → no dates key at all
    assert "dates" not in payload_by_title["Book studio"]
    # unparseable due → task still created, just without a date
    assert "dates" not in payload_by_title["Odd date"]


def test_second_export_updates_not_duplicates(db_seeded):
    db = db_seeded
    rid = _seed_recording(
        db,
        my_todos=[TodoItem(task="Do A"), TodoItem(task="Do B")],
        action_items_others=[ActionItemOther(who="Sam", task="Send doc")],
        follow_ups=["Revisit pricing"],
        manual_notes="call Bob",
    )
    fake = FakeClient()
    export_recording(db, fake, rid, parent_id="P", assignees={0: "c-sam"})

    report = export_recording(db, fake, rid, parent_id="P", assignees={0: "c-sam"})

    assert report.updated is True
    assert report.created_tasks == 0
    assert report.failures == []
    assert len(fake.create_project_calls) == 1  # not a 2nd create
    assert len(fake.update_project_calls) == 1

    assert fake.delete_attachment_calls == ["att1"]
    assert len(fake.upload_attachment_calls) == 2  # replaced

    assert len(fake.create_comment_calls) == 1  # not a 2nd comment

    tasks = WrikeTaskRepo(db).list_for_recording(rid)
    assert len(tasks) == 4

    proj_row = WrikeProjectRepo(db).get(rid)
    assert proj_row.attachment_id == "att2"


def test_partial_failure_records_success_and_resumes(db_seeded):
    db = db_seeded
    rid = _seed_recording(
        db,
        my_todos=[TodoItem(task="Do A"), TodoItem(task="Do B")],
        action_items_others=[ActionItemOther(who="Sam", task="Send doc")],
        follow_ups=["Revisit pricing"],
        manual_notes="call Bob",
    )
    flaky = FakeClient(fail_first_create_task=True)

    first = export_recording(db, flaky, rid, parent_id="P", assignees={0: "c-sam"})

    assert first.failures
    assert any("my/0" in f for f in first.failures)
    assert first.project_id == "prj1"
    assert first.created_tasks == 3  # all but the failed one
    assert len(flaky.upload_attachment_calls) == 1  # attachment still done
    assert len(flaky.create_comment_calls) == 1  # notes still posted

    tasks = WrikeTaskRepo(db).list_for_recording(rid)
    assert {(t.kind, t.todo_index) for t in tasks} == {
        ("my", 1), ("other", 0), ("follow_up", 0),
    }

    healthy = FakeClient()
    second = export_recording(db, healthy, rid, parent_id="P", assignees={0: "c-sam"})

    assert second.failures == []
    assert second.created_tasks == 1  # only the previously-failed task
    assert len(healthy.create_task_calls) == 1
    assert healthy.create_task_calls[0][1]["title"].startswith("Do A")
    assert len(healthy.create_comment_calls) == 0  # already posted, guarded

    tasks = WrikeTaskRepo(db).list_for_recording(rid)
    assert len(tasks) == 4  # no duplicates of the ones that already succeeded
    kinds = {(t.kind, t.todo_index) for t in tasks}
    assert kinds == {("my", 0), ("my", 1), ("other", 0), ("follow_up", 0)}


def test_new_todo_added_on_repush(db_seeded):
    db = db_seeded
    rid = _seed_recording(
        db,
        my_todos=[TodoItem(task="Do A"), TodoItem(task="Do B")],
        action_items_others=[],
        follow_ups=[],
    )
    fake = FakeClient()
    first = export_recording(db, fake, rid, parent_id="P", assignees={})
    assert first.created_tasks == 2

    # Re-summarization adds a 3rd my_todo.
    SummaryRepo(db).upsert(Summary(
        recording_id=rid, title="Q3 sync", one_line="Aligned on x.",
        summary="Discussed x.", key_decisions=["Ship in July"],
        my_todos=[TodoItem(task="Do A"), TodoItem(task="Do B"), TodoItem(task="Do C")],
        action_items_others=[], follow_ups=[], topics=["billing"],
        generated_at="2026-07-01T10:31:00+00:00", model_used="claude-sonnet-4-6",
    ))

    second = export_recording(db, fake, rid, parent_id="P", assignees={})

    assert second.created_tasks == 1  # only the new one
    assert second.failures == []

    tasks = WrikeTaskRepo(db).list_for_recording(rid)
    assert len(tasks) == 3
    kinds = {(t.kind, t.todo_index) for t in tasks}
    assert kinds == {("my", 0), ("my", 1), ("my", 2)}


def test_task_contexts_set_description_only_when_supplied(db_seeded):
    db = db_seeded
    rid = _seed_recording(
        db,
        my_todos=[TodoItem(task="Do A"), TodoItem(task="Do B")],
        action_items_others=[ActionItemOther(who="Sam", task="Send doc")],
        follow_ups=["Revisit pricing"],
    )
    fake = FakeClient()

    report = export_recording(
        db, fake, rid, parent_id="P", assignees={0: "c-sam"},
        task_contexts={
            ("my", 0): "You committed to Do A after the demo.",
            ("follow_up", 0): "Pricing needs a second look next quarter.",
        },
    )

    assert report.failures == []
    payload_by_title = {p["title"]: p for _folder, p in fake.create_task_calls}

    assert payload_by_title["Do A"]["description"] == "You committed to Do A after the demo."
    assert payload_by_title["Revisit pricing"]["description"] == (
        "Pricing needs a second look next quarter."
    )
    # No context supplied for these two -> no description key at all.
    assert "description" not in payload_by_title["Do B"]
    assert "description" not in payload_by_title["Sam: Send doc"]


def test_task_contexts_none_means_no_descriptions_anywhere(db_seeded):
    db = db_seeded
    rid = _seed_recording(
        db,
        my_todos=[TodoItem(task="Do A")],
        action_items_others=[],
        follow_ups=[],
    )
    fake = FakeClient()

    report = export_recording(db, fake, rid, parent_id="P", assignees={})

    assert report.failures == []
    payload = fake.create_task_calls[0][1]
    assert "description" not in payload
