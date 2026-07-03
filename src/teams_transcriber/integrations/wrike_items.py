"""SyncItem model + conversion from a Recording/Summary into a stable list.

`recording_to_sync_items(db, rid)` is the single conversion point so the
planner, the orchestrator, and any tests all see the same item ordering.
Ordering matters because the planner uses positional indices and the
WrikeTaskRepo uses (kind, todo_index) for idempotency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from teams_transcriber.storage import SummaryRepo
from teams_transcriber.storage.db import Database

SyncKind = Literal["summary", "decisions", "my_todo", "action_other", "follow_up"]


@dataclass(slots=True)
class SyncItem:
    kind: SyncKind
    index: int                       # 0 for singletons, per-list-element otherwise
    text: str
    suggested_who: str | None = None  # action_other only


def _decisions_block(decisions: list[str]) -> str:
    return "\n".join(f"- {d}" for d in decisions)


def recording_to_sync_items(db: Database, recording_id: int) -> list[SyncItem]:
    """Stable ordering: summary, decisions, my_todos (in order),
    action_items_others (in order), follow_ups (in order)."""
    summary = SummaryRepo(db).get(recording_id)
    if summary is None:
        return []
    items: list[SyncItem] = []
    if summary.summary:
        items.append(SyncItem(kind="summary", index=0, text=summary.summary))
    if summary.key_decisions:
        items.append(SyncItem(
            kind="decisions", index=0, text=_decisions_block(summary.key_decisions),
        ))
    for i, td in enumerate(summary.my_todos):
        title = td.task + (f" (due {td.due})" if td.due else "")
        items.append(SyncItem(kind="my_todo", index=i, text=title))
    for j, ai in enumerate(summary.action_items_others):
        title = ai.task + (f" (due {ai.due})" if ai.due else "")
        items.append(SyncItem(
            kind="action_other", index=j, text=title, suggested_who=ai.who,
        ))
    for k, f in enumerate(summary.follow_ups):
        items.append(SyncItem(kind="follow_up", index=k, text=f))
    return items
