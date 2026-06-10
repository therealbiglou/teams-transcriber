"""sync_items: idempotent multi-destination sync orchestrator."""

from __future__ import annotations

from typing import Any

from teams_transcriber.integrations.wrike_items import SyncItem
from teams_transcriber.integrations.wrike_sync import PlanRow, SyncReport, sync_items
from teams_transcriber.storage import (
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    build_database,
)
from teams_transcriber.storage.wrike import WrikeTaskRepo


class _FakeClient:
    def __init__(self) -> None:
        self.tasks: list[tuple[str, dict[str, Any]]] = []
        self.comments: list[tuple[str, str, str]] = []
        self._next_task_id = 100
        self._next_comment_id = 1000

    def create_task(self, folder_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.tasks.append((folder_id, payload))
        tid = str(self._next_task_id); self._next_task_id += 1
        return {"id": tid}

    def create_comment(self, *, entity_type: str, entity_id: str, text: str) -> str:
        self.comments.append((entity_type, entity_id, text))
        cid = f"C{self._next_comment_id}"; self._next_comment_id += 1
        return cid


def _seed_recording(tmp_path):
    db = build_database(tmp_path / "sync.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="Q3 sync",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    return db, rec.id


def test_sync_items_creates_tasks_and_comments(tmp_path) -> None:
    db, rid = _seed_recording(tmp_path)
    plan = [
        PlanRow(
            item=SyncItem(kind="summary", index=0, text="we aligned"),
            folder_id="F_PROJ", format="comment", assignee_id=None,
        ),
        PlanRow(
            item=SyncItem(kind="my_todo", index=0, text="Email J"),
            folder_id="F_TODOS", format="task", assignee_id=None,
        ),
        PlanRow(
            item=SyncItem(kind="action_other", index=0,
                          text="Migration doc", suggested_who="Sarah"),
            folder_id="F_PROJ", format="task", assignee_id="200",
        ),
    ]
    client = _FakeClient()
    report = sync_items(db, rid, plan, client=client)
    assert report.created_tasks == 2
    assert report.created_comments == 1
    assert report.skipped_already_synced == 0
    assert report.failures == []

    folders = sorted(f for f, _ in client.tasks)
    assert folders == ["F_PROJ", "F_TODOS"]
    assert client.comments == [("folder", "F_PROJ", "we aligned")]
    # Persisted kinds use the canonical wrike_tasks taxonomy: a SyncItem
    # 'my_todo' stores as 'my' and 'action_other' as 'other' (so Phase-11
    # rows and the close-loop completion keep lining up); singletons verbatim.
    rows = sorted(
        WrikeTaskRepo(db).list_for_recording(rid),
        key=lambda r: (r.kind, r.todo_index),
    )
    assert [(r.kind, r.format, r.assignee_id) for r in rows] == [
        ("my", "task", None),          # was SyncItem 'my_todo'
        ("other", "task", "200"),      # was SyncItem 'action_other'
        ("summary", "comment", None),
    ]
    db.close()


def test_sync_items_is_idempotent(tmp_path) -> None:
    db, rid = _seed_recording(tmp_path)
    plan = [
        PlanRow(
            item=SyncItem(kind="my_todo", index=0, text="t"),
            folder_id="F", format="task", assignee_id=None,
        ),
    ]
    client = _FakeClient()
    sync_items(db, rid, plan, client=client)
    report2 = sync_items(db, rid, plan, client=client)
    assert report2.created_tasks == 0
    assert report2.skipped_already_synced == 1
    assert len(client.tasks) == 1
    db.close()


def test_sync_items_partial_failure_continues(tmp_path) -> None:
    db, rid = _seed_recording(tmp_path)

    class _PartFail:
        def __init__(self) -> None:
            self.tasks = 0
            self.comments = 0

        def create_task(self, folder_id, payload):
            self.tasks += 1
            if "BOOM" in payload["title"]:
                raise RuntimeError("api 500")
            return {"id": "T" + str(self.tasks)}

        def create_comment(self, *, entity_type, entity_id, text):
            self.comments += 1
            return "C" + str(self.comments)

    plan = [
        PlanRow(item=SyncItem(kind="my_todo", index=0, text="ok-1"),
                folder_id="F", format="task", assignee_id=None),
        PlanRow(item=SyncItem(kind="my_todo", index=1, text="BOOM"),
                folder_id="F", format="task", assignee_id=None),
        PlanRow(item=SyncItem(kind="my_todo", index=2, text="ok-2"),
                folder_id="F", format="task", assignee_id=None),
    ]
    client = _PartFail()
    report = sync_items(db, rid, plan, client=client)
    assert report.created_tasks == 2
    assert len(report.failures) == 1
    assert "BOOM" in report.failures[0][0].text
    db.close()


def test_sync_items_carries_assignee_for_action_other(tmp_path) -> None:
    db, rid = _seed_recording(tmp_path)
    plan = [
        PlanRow(
            item=SyncItem(kind="action_other", index=0,
                          text="task body", suggested_who="Sarah"),
            folder_id="F", format="task", assignee_id="200",
        ),
    ]
    client = _FakeClient()
    sync_items(db, rid, plan, client=client)
    folder_id, payload = client.tasks[0]
    assert payload["responsibles"] == ["200"]
    db.close()


def test_sync_items_respects_legacy_phase11_rows(tmp_path) -> None:
    """A Phase-11 row (kind='my') must lock the equivalent new 'my_todo' item,
    so re-syncing a meeting first synced under the old single-folder path does
    not double-create the task."""
    from teams_transcriber.storage.wrike import WrikeTaskRepo, WrikeTaskRow

    db, rid = _seed_recording(tmp_path)
    # Simulate a meeting already synced under Phase 11: my-todo 0 -> 'my'.
    WrikeTaskRepo(db).insert(WrikeTaskRow(
        id=None, recording_id=rid, kind="my", todo_index=0,
        wrike_task_id="OLD123", wrike_folder_id="F_OLD",
        created_at="2026-06-08T00:00:00+00:00", last_synced_done=False,
    ))
    plan = [
        PlanRow(
            item=SyncItem(kind="my_todo", index=0, text="Email J"),
            folder_id="F_NEW", format="task", assignee_id=None,
        ),
    ]
    client = _FakeClient()
    report = sync_items(db, rid, plan, client=client)
    assert report.created_tasks == 0
    assert report.skipped_already_synced == 1
    assert client.tasks == []  # no duplicate task created
    db.close()
