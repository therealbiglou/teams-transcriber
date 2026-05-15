"""Repository for AI-generated summaries (one per recording).

JSON fields are stored as TEXT and (de)serialized at the boundary so the DB schema
stays simple. The Summary dataclass is the canonical in-memory shape.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import (
    ActionItemOther,
    Summary,
    TodoItem,
)


# Module-level row-mapper, mirroring the convention from recordings.py / transcripts.py.
def _row_to_summary(row: sqlite3.Row) -> Summary:
    return Summary(
        recording_id=row["recording_id"],
        one_line=row["one_line"],
        summary=row["summary"],
        key_decisions=json.loads(row["key_decisions_json"]),
        my_todos=[TodoItem(**d) for d in json.loads(row["my_todos_json"])],
        action_items_others=[
            ActionItemOther(**d) for d in json.loads(row["action_items_others_json"])
        ],
        follow_ups=json.loads(row["follow_ups_json"]),
        topics=json.loads(row["topics_json"]),
        generated_at=row["generated_at"],
        model_used=row["model_used"],
    )


class SummaryRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, summary: Summary) -> None:
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO summaries (
                    recording_id, one_line, summary,
                    key_decisions_json, my_todos_json, action_items_others_json,
                    follow_ups_json, topics_json,
                    generated_at, model_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recording_id) DO UPDATE SET
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
