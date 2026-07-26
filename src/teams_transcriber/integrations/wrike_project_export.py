"""Create-once, hands-off Wrike project export for one recording.

Pure orchestration against a client Protocol + the db repos. Idempotent:
re-running updates the description, replaces the transcript attachment, adds
only tasks not already mapped, and posts the notes comment once. Per-step
failures are collected; what succeeded is persisted so a re-push resumes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from teams_transcriber.integrations.wrike_project_body import (
    build_description,
    build_task_description,
    build_transcript_md,
)
from teams_transcriber.storage.db import Database
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.summaries import SummaryRepo
from teams_transcriber.storage.transcripts import TranscriptRepo
from teams_transcriber.storage.wrike import (
    WrikeProjectRepo,
    WrikeTaskRepo,
    WrikeTaskRow,
)

logger = logging.getLogger(__name__)
_TAG_RE = re.compile(r"<[^>]+>")


class _ClientProto(Protocol):
    def create_project(self, parent_id: str, title: str, description: str) -> dict[str, Any]: ...
    def update_project(self, project_id: str, *, description: str) -> dict[str, Any]: ...
    def upload_attachment(self, entity_id: str, filename: str, content: bytes) -> str: ...
    def delete_attachment(self, attachment_id: str) -> None: ...
    def create_task(self, folder_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def create_comment(self, *, entity_type: str, entity_id: str, text: str) -> str: ...


@dataclass(slots=True)
class ExportReport:
    project_id: str | None = None
    permalink: str | None = None
    created_tasks: int = 0
    updated: bool = False
    failures: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _plain(text: str | None) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _due_date(raw: str | None) -> str | None:
    """Normalize an LLM-provided due value to a Wrike ``yyyy-MM-dd`` date.

    Returns None (task gets no due date, rather than failing) if the value is
    absent or not a parseable ISO date — the summary field is free-form.
    """
    if not raw:
        return None
    candidate = raw.strip()[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


def export_recording(
    db: Database, client: _ClientProto, recording_id: int,
    *, parent_id: str, assignees: dict[int, str | None],
    task_contexts: dict[tuple[str, int], str] | None = None,
) -> ExportReport:
    report = ExportReport()
    rec = RecordingRepo(db).get(recording_id)
    summary = SummaryRepo(db).get(recording_id)
    if rec is None or summary is None:
        report.failures.append("recording or summary missing")
        return report

    proj_repo = WrikeProjectRepo(db)
    task_repo = WrikeTaskRepo(db)
    existing = proj_repo.get(recording_id)
    title = rec.display_title or summary.title or "Meeting"
    description = build_description(summary)

    # 1. find-or-create project
    if existing is None:
        try:
            folder = client.create_project(parent_id, title, description)
            project_id = str(folder["id"])
            proj_repo.upsert(recording_id, project_id=project_id,
                             permalink=folder.get("permalink"))
            report.project_id = project_id
            report.permalink = folder.get("permalink")
        except Exception as exc:
            logger.exception("wrike create_project failed for %d", recording_id)
            report.failures.append(f"create project: {exc}")
            return report
    else:
        project_id = existing.project_id
        report.project_id = project_id
        report.permalink = existing.permalink
        report.updated = True
        try:
            client.update_project(project_id, description=description)
            proj_repo.upsert(recording_id, project_id=project_id)
        except Exception as exc:
            logger.exception("wrike update_project failed for %d", recording_id)
            report.failures.append(f"update description: {exc}")

    # 2. transcript attachment (replace on re-push)
    try:
        md = build_transcript_md(TranscriptRepo(db).list_for_recording(recording_id))
        old = proj_repo.get(recording_id)
        if old and old.attachment_id:
            try:
                client.delete_attachment(old.attachment_id)
            except Exception:
                logger.warning("could not delete old attachment %s", old.attachment_id)
        att_id = client.upload_attachment(project_id, "transcript.md", md.encode("utf-8"))
        proj_repo.set_attachment(recording_id, att_id)
    except Exception as exc:
        logger.exception("wrike transcript attach failed for %d", recording_id)
        report.failures.append(f"transcript: {exc}")

    # 3. tasks (my_todos, action_items_others[assigned], follow_ups) — add only new
    def _ensure_task(
        kind: str, index: int, name: str, assignee: str | None,
        due: str | None = None,
    ) -> None:
        if task_repo.get(recording_id, kind, index) is not None:
            return
        payload: dict[str, Any] = {"title": name}
        if assignee:
            payload["responsibles"] = [assignee]
        context = (task_contexts or {}).get((kind, index))
        if context:
            payload["description"] = build_task_description(context)
        d = _due_date(due)
        if d:
            # A single-day Planned task carries a real Wrike due date; a due-only
            # date would coerce to a Milestone (diamond marker) instead.
            payload["dates"] = {"type": "Planned", "start": d, "due": d}
        try:
            created = client.create_task(project_id, payload)
            task_repo.insert(WrikeTaskRow(
                id=None, recording_id=recording_id, kind=kind, todo_index=index,
                wrike_task_id=str(created["id"]), wrike_folder_id=project_id,
                created_at=_now(), last_synced_done=False, format="task",
                assignee_id=assignee,
            ))
            report.created_tasks += 1
        except Exception as exc:
            logger.exception("wrike create_task failed %s/%d", kind, index)
            report.failures.append(f"task {kind}/{index}: {exc}")

    for i, td in enumerate(summary.my_todos):
        _ensure_task("my", i, td.task, None, due=td.due)
    for j, ai in enumerate(summary.action_items_others):
        title_txt = f"{ai.who}: {ai.task}" if ai.who else ai.task
        _ensure_task("other", j, title_txt, assignees.get(j), due=ai.due)
    for k, f in enumerate(summary.follow_ups):
        # "follow_up" (not "follow") — matches the wrike_tasks.kind CHECK constraint
        # from schema v6, which already reserves this literal for follow-up items.
        _ensure_task("follow_up", k, f, None)

    # 4. notes comment (once)
    notes = _plain(rec.manual_notes)
    row = proj_repo.get(recording_id)
    if notes and (row is None or not row.notes_comment_id):
        try:
            cid = client.create_comment(entity_type="folder", entity_id=project_id, text=notes)
            proj_repo.set_notes_comment(recording_id, cid)
        except Exception as exc:
            logger.exception("wrike notes comment failed for %d", recording_id)
            report.failures.append(f"notes: {exc}")

    return report
