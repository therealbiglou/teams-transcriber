"""Repository for AI-generated summaries (one per recording).

JSON fields are stored as TEXT and (de)serialized at the boundary so the DB schema
stays simple. The Summary dataclass is the canonical in-memory shape.

Note: spec §7.4 calls for global search to also index summaries (one_line + summary
fields). That's deferred to Phase 3 (UI / search), implemented as a v2 schema migration
that adds a `summaries_fts` virtual table with its own AFTER INSERT/DELETE/UPDATE
triggers, and a `search_all` method that merges transcript and summary hits.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import (
    ActionItemOther,
    Summary,
    TodoItem,
)


def _load_json_list(raw: str | None) -> list[Any]:
    """Parse a JSON blob expected to be a list. Returns [] on any deviation."""
    if raw is None:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return value


def _filter_dataclass_kwargs(cls: type, d: dict[str, Any]) -> dict[str, Any]:
    """Drop dict keys that aren't fields of the given dataclass — tolerates schema drift."""
    if not isinstance(d, dict):
        return {}
    fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
    return {k: v for k, v in d.items() if k in fields}


# Module-level row-mapper, mirroring the convention from recordings.py / transcripts.py.
def _row_to_summary(row: sqlite3.Row) -> Summary:
    return Summary(
        recording_id=row["recording_id"],
        title=row["title"],
        one_line=row["one_line"],
        summary=row["summary"],
        key_decisions=_load_json_list(row["key_decisions_json"]),
        my_todos=[
            TodoItem(**_filter_dataclass_kwargs(TodoItem, d))
            for d in _load_json_list(row["my_todos_json"])
            if isinstance(d, dict)
        ],
        action_items_others=[
            ActionItemOther(**_filter_dataclass_kwargs(ActionItemOther, d))
            for d in _load_json_list(row["action_items_others_json"])
            if isinstance(d, dict)
        ],
        follow_ups=_load_json_list(row["follow_ups_json"]),
        topics=_load_json_list(row["topics_json"]),
        generated_at=row["generated_at"],
        model_used=row["model_used"],
    )


class SummaryRepo:
    """CRUD for AI-generated meeting summaries (one per recording)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, summary: Summary) -> None:
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO summaries (
                    recording_id, title, one_line, summary,
                    key_decisions_json, my_todos_json, action_items_others_json,
                    follow_ups_json, topics_json,
                    generated_at, model_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recording_id) DO UPDATE SET
                    title = excluded.title,
                    one_line = excluded.one_line,
                    summary = excluded.summary,
                    key_decisions_json = excluded.key_decisions_json,
                    my_todos_json = excluded.my_todos_json,
                    action_items_others_json = excluded.action_items_others_json,
                    follow_ups_json = excluded.follow_ups_json,
                    topics_json = excluded.topics_json,
                    generated_at = excluded.generated_at,
                    model_used = excluded.model_used
                """,
                (
                    summary.recording_id,
                    summary.title,
                    summary.one_line,
                    summary.summary,
                    json.dumps(summary.key_decisions),
                    json.dumps([asdict(t) for t in summary.my_todos]),
                    json.dumps([asdict(a) for a in summary.action_items_others]),
                    json.dumps(summary.follow_ups),
                    json.dumps(summary.topics),
                    summary.generated_at,
                    summary.model_used,
                ),
            )
            conn.commit()

    def get(self, recording_id: int) -> Summary | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM summaries WHERE recording_id = ?", (recording_id,)
            ).fetchone()
        return _row_to_summary(row) if row is not None else None

    def delete(self, recording_id: int) -> None:
        """Remove the summary for a recording. Idempotent — no-op if none exists."""
        with self._db.connect() as conn:
            conn.execute("DELETE FROM summaries WHERE recording_id = ?", (recording_id,))
            conn.commit()
