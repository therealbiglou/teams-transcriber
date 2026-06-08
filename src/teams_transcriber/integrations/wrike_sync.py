"""Per-recording Wrike sync.

`sync_recording(db, client, recording_id, *, folder_id)` reads the Summary
and existing `wrike_tasks` mappings, creates one task per unmapped todo
(mine + others), and persists the new mappings. Idempotent: a second call
with the same recording is a no-op for already-mapped todos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.summaries import SummaryRepo
from teams_transcriber.storage.wrike import WrikeTaskRepo, WrikeTaskRow

logger = logging.getLogger(__name__)


class _ClientProto(Protocol):
    def test_connection(self) -> dict[str, Any]: ...
    def list_contacts(self) -> list[dict[str, Any]]: ...
    def create_task(self, folder_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def complete_task(self, task_id: str, *, done: bool) -> dict[str, Any]: ...


@dataclass(slots=True)
class SyncResult:
    created_my: int = 0
    created_other: int = 0
    assigned_other: int = 0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _full_name(contact: dict[str, Any]) -> str:
    first = (contact.get("firstName") or "").strip()
    last = (contact.get("lastName") or "").strip()
    return f"{first} {last}".strip()


def _match_contact(name: str, contacts: list[dict[str, Any]]) -> str | None:
    """Case-insensitive exact full-name match. Returns contact id, or None
    on no match / ambiguous match."""
    needle = name.strip().lower()
    hits = [c for c in contacts if _full_name(c).lower() == needle]
    if len(hits) == 1:
        return str(hits[0]["id"])
    return None


def _build_description(meeting_title: str, started_at: str, context: str | None) -> str:
    parts = [f"From meeting: {meeting_title} ({started_at})"]
    if context:
        parts.append(context)
    return "\n\n".join(parts)


def sync_recording(
    db: Database,
    client: _ClientProto,
    recording_id: int,
    *,
    folder_id: str,
) -> SyncResult:
    """Create Wrike tasks for any todos on `recording_id` not already mapped."""
    summary = SummaryRepo(db).get(recording_id)
    if summary is None:
        return SyncResult()
    rec = RecordingRepo(db).get(recording_id)
    rec_title = (rec.display_title if rec else None) or summary.title or "Meeting"
    started_at = (rec.started_at if rec else "")[:10]

    task_repo = WrikeTaskRepo(db)
    existing = {(r.kind, r.todo_index) for r in task_repo.list_for_recording(recording_id)}
    res = SyncResult()

    self_id = client.test_connection().get("id")

    for i, td in enumerate(summary.my_todos):
        if ("my", i) in existing:
            continue
        payload: dict[str, Any] = {
            "title": td.task,
            "description": _build_description(rec_title, started_at, td.context),
            "status": "Active",
        }
        if td.due:
            payload["dates"] = {"due": td.due}
        if self_id:
            payload["responsibles"] = [self_id]
        created = client.create_task(folder_id, payload)
        task_repo.insert(WrikeTaskRow(
            id=None, recording_id=recording_id, kind="my", todo_index=i,
            wrike_task_id=str(created["id"]), wrike_folder_id=folder_id,
            created_at=_now_iso(), last_synced_done=False,
        ))
        res.created_my += 1

    if summary.action_items_others:
        contacts = client.list_contacts()
    else:
        contacts = []
    for j, ai in enumerate(summary.action_items_others):
        if ("other", j) in existing:
            continue
        matched_id = _match_contact(ai.who, contacts)
        payload = {
            "title": f"For {ai.who}: {ai.task}",
            "description": _build_description(rec_title, started_at, None),
            "status": "Active",
        }
        if ai.due:
            payload["dates"] = {"due": ai.due}
        if matched_id:
            payload["responsibles"] = [matched_id]
            res.assigned_other += 1
        created = client.create_task(folder_id, payload)
        task_repo.insert(WrikeTaskRow(
            id=None, recording_id=recording_id, kind="other", todo_index=j,
            wrike_task_id=str(created["id"]), wrike_folder_id=folder_id,
            created_at=_now_iso(), last_synced_done=False,
        ))
        res.created_other += 1

    return res
