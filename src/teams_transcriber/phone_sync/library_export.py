"""Build the desktop→phone library export (the phone's full mirror).

Pure: db in, {relative name: JSON text} out. The sync engine decides how the
files reach the phone; tests read the dict directly.
"""

from __future__ import annotations

import json

from teams_transcriber import __version__
from teams_transcriber.phone_sync.contract import build_manifest
from teams_transcriber.storage import (
    ChatRepo, Database, PhoneImportRepo, RecordingRepo, SummaryRepo,
    TodoStateRepo, TranscriptRepo,
)

_EXPORT_LIMIT = 1000  # matches the app's personal-scale assumptions


def build_library(db: Database, *, now_iso: str) -> dict[str, str]:
    rec_repo = RecordingRepo(db)
    sum_repo = SummaryRepo(db)
    todo_repo = TodoStateRepo(db)
    tr_repo = TranscriptRepo(db)
    chat_repo = ChatRepo(db)
    phone_sources = PhoneImportRepo(db).source_for_recordings()

    files: dict[str, str] = {
        "library/manifest.json": build_manifest(__version__, now_iso),
    }
    meetings: list[dict] = []

    for rec in rec_repo.list_recent(limit=_EXPORT_LIMIT):
        if rec.id is None:
            continue
        summary = sum_repo.get(rec.id)
        states = {s.todo_index: s for s in todo_repo.list_for_recording(rec.id)}
        todo_count = len(summary.my_todos) if summary else 0
        todos_done = sum(1 for s in states.values() if s.done)
        meetings.append({
            "id": rec.id,
            "title": rec.display_title or rec.detected_title or "Untitled meeting",
            "started_at": rec.started_at,
            "duration_ms": rec.duration_ms,
            "status": rec.status.value,
            "one_line": summary.one_line if summary else None,
            "source": phone_sources.get(rec.id, rec.source.value),
            "todo_count": todo_count,
            "todos_done": todos_done,
        })
        if summary is None:
            continue
        files[f"library/meetings/{rec.id}.json"] = json.dumps({
            "id": rec.id,
            "summary": summary.summary,
            "key_decisions": summary.key_decisions,
            "my_todos": [
                {
                    "index": i,
                    "task": td.task,
                    "due": td.due,
                    "done": bool(states.get(i) and states[i].done),
                    "done_at": states[i].done_at if i in states else None,
                }
                for i, td in enumerate(summary.my_todos)
            ],
            "action_items_others": [
                {"who": a.who, "task": a.task, "due": a.due}
                for a in summary.action_items_others
            ],
            "follow_ups": summary.follow_ups,
            "transcript": [
                {"start_ms": s.start_ms, "channel": s.channel.value, "text": s.text}
                for s in tr_repo.list_for_recording(rec.id)
            ],
            "chat": [
                {"role": m.role, "content": m.content, "created_at": m.created_at}
                for m in chat_repo.list_for_recording(rec.id)
            ],
        }, indent=2)

    files["library/meetings.json"] = json.dumps(meetings, indent=2)
    return files
