"""Per-recording Wrike sync.

`sync_recording(db, client, recording_id, *, folder_id)` reads the Summary
and existing `wrike_tasks` mappings, creates one task per unmapped todo
(mine + others), and persists the new mappings. Idempotent: a second call
with the same recording is a no-op for already-mapped todos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from teams_transcriber.integrations.wrike_items import SyncItem
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
    def create_comment(self, *, entity_type: str, entity_id: str, text: str) -> str: ...


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


def _safe_iso_date(due: str | None) -> str | None:
    """Return `due` as YYYY-MM-DD if it parses as a real ISO date, else None.

    The LLM produces freeform due-date strings ("next Tuesday", "by EOD Fri",
    "Q3", or sometimes a real ISO date). Wrike's `dates.due` field requires a
    strict ISO date string; anything else triggers a 400
    `Parameter 'dates' value is invalid`. We accept only what Wrike will
    accept, and the caller appends the original text to the description as a
    fallback so the information isn't lost.
    """
    if not due:
        return None
    s = due.strip()
    if not s:
        return None
    # YYYY-MM-DD exact.
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except ValueError:
        pass
    # Full ISO datetime (with or without timezone) — normalize to date-only.
    try:
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _payload_with_due(
    base_description: str,
    due: str | None,
) -> tuple[str, dict[str, str] | None]:
    """Decide how to ship a possibly-non-ISO due string.

    Returns `(description, dates_field)` where `dates_field` is either a dict
    safe to put under `payload["dates"]` (e.g. `{"due": "2026-06-15"}`) or
    None if the date wasn't parseable. When unparseable, the original raw
    string is appended to the description so the user still sees it.
    """
    safe = _safe_iso_date(due)
    if safe:
        return base_description, {"due": safe}
    if due and due.strip():
        return base_description + f"\n\nDue (as written): {due.strip()}", None
    return base_description, None


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

    self_id = client.test_connection().get("id") if summary.my_todos else None

    for i, td in enumerate(summary.my_todos):
        if ("my", i) in existing:
            continue
        base_desc = _build_description(rec_title, started_at, td.context)
        desc, dates = _payload_with_due(base_desc, td.due)
        payload: dict[str, Any] = {
            "title": td.task,
            "description": desc,
            "status": "Active",
        }
        if dates:
            payload["dates"] = dates
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
        base_desc = _build_description(rec_title, started_at, None)
        desc, dates = _payload_with_due(base_desc, ai.due)
        payload = {
            "title": f"For {ai.who}: {ai.task}",
            "description": desc,
            "status": "Active",
        }
        if dates:
            payload["dates"] = dates
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


@dataclass(slots=True)
class PlanRow:
    item: SyncItem
    folder_id: str
    format: str       # "task" | "comment"
    assignee_id: str | None


@dataclass(slots=True)
class SyncReport:
    created_tasks: int = 0
    created_comments: int = 0
    skipped_already_synced: int = 0
    failures: list[tuple[SyncItem, str]] = field(default_factory=list)


# The `wrike_tasks.kind` taxonomy predates the multi-destination SyncKind.
# Phase 11 stored my-todos as 'my' and action-items-for-others as 'other', and
# two existing behaviors key off those legacy values: the close-loop completion
# (`app._wrike_close_loop_changes` filters `kind == "my"`) and the
# "locked when already synced" guard (a recording synced under Phase 11 must
# show as synced in the new planner). So multi-dest rows REUSE the legacy
# values — a SyncItem 'my_todo' persists as 'my', 'action_other' as 'other'.
# The three new singleton kinds (summary/decisions/follow_up) have no legacy
# equivalent and store verbatim. Callers comparing a SyncItem kind against a
# persisted row MUST convert through these helpers first.
_SYNC_KIND_TO_DB = {"my_todo": "my", "action_other": "other"}
_DB_KIND_TO_SYNC = {v: k for k, v in _SYNC_KIND_TO_DB.items()}


def sync_kind_to_db_kind(kind: str) -> str:
    """Map a `wrike_items.SyncKind` to the stored `wrike_tasks.kind` value."""
    return _SYNC_KIND_TO_DB.get(kind, kind)


def db_kind_to_sync_kind(kind: str) -> str:
    """Inverse of `sync_kind_to_db_kind`: stored kind -> SyncKind.

    Used when building the planner's already-synced set from persisted rows so
    it lines up with the SyncKind-keyed items the planner renders.
    """
    return _DB_KIND_TO_SYNC.get(kind, kind)


def sync_items(
    db: Database,
    recording_id: int,
    plan: list[PlanRow],
    *,
    client: _ClientProto,
) -> SyncReport:
    """Run the planner's PlanRow list. Idempotent on (recording_id, kind, index).

    Tasks route to ``create_task`` (with optional responsibles), comments route
    to ``create_comment`` on the destination folder. The resulting Wrike entity
    id (task id or comment id) is persisted back to ``wrike_tasks`` along with
    the per-row ``format`` and ``assignee_id``.

    On per-row failure we accumulate the error and continue with the rest;
    callers surface partial successes via the report.
    """
    rec = RecordingRepo(db).get(recording_id)
    rec_title = (rec.display_title if rec else None) or "Meeting"
    started_at = (rec.started_at if rec else "")[:10]

    task_repo = WrikeTaskRepo(db)
    already = {
        (r.kind, r.todo_index) for r in task_repo.list_for_recording(recording_id)
    }
    report = SyncReport()

    for row in plan:
        item = row.item
        db_kind = sync_kind_to_db_kind(item.kind)
        if (db_kind, item.index) in already:
            report.skipped_already_synced += 1
            continue
        try:
            if row.format == "task":
                payload: dict[str, Any] = {
                    "title": (
                        item.text
                        if len(item.text) <= 100
                        else item.text[:97] + "…"
                    ),
                    "description": _build_description(rec_title, started_at, None),
                    "status": "Active",
                }
                if row.assignee_id:
                    payload["responsibles"] = [row.assignee_id]
                created = client.create_task(row.folder_id, payload)
                ref_id = str(created["id"])
                report.created_tasks += 1
            elif row.format == "comment":
                ref_id = client.create_comment(
                    entity_type="folder",
                    entity_id=row.folder_id,
                    text=item.text,
                )
                report.created_comments += 1
            else:
                raise ValueError(f"unknown format: {row.format!r}")

            task_repo.insert(WrikeTaskRow(
                id=None, recording_id=recording_id,
                kind=db_kind, todo_index=item.index,
                wrike_task_id=ref_id, wrike_folder_id=row.folder_id,
                created_at=_now_iso(), last_synced_done=False,
                format=row.format, assignee_id=row.assignee_id,
            ))
        except Exception as exc:  # noqa: BLE001 — accumulate, keep going
            logger.warning(
                "sync_items: %s/%d failed: %s", item.kind, item.index, exc,
            )
            report.failures.append((item, str(exc)))

    return report
