"""Repository for `todo_state` — tracks checkbox state for each my_todo across re-summaries."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import TodoState


# Module-level row-mapper (see recordings.py for the convention).
def _row_to_todo(row: sqlite3.Row) -> TodoState:
    return TodoState(
        id=row["id"],
        recording_id=row["recording_id"],
        todo_index=row["todo_index"],
        task_text=row["task_text"],
        done=bool(row["done"]),
        done_at=row["done_at"],
    )


class TodoStateRepo:
    """CRUD for todo_state — per-row done flags that survive re-summarization."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(
        self,
        recording_id: int,
        todo_index: int,
        task_text: str,
        done: bool,
    ) -> None:
        done_at = datetime.now(UTC).isoformat() if done else None
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO todo_state (recording_id, todo_index, task_text, done, done_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(recording_id, todo_index) DO UPDATE SET
                    task_text = excluded.task_text,
                    done      = excluded.done,
                    done_at   = excluded.done_at
                """,
                (recording_id, todo_index, task_text, int(done), done_at),
            )
            conn.commit()

    def mark_done(
        self,
        recording_id: int,
        todo_index: int,
        done: bool,
        *,
        task_text: str | None = None,
    ) -> None:
        """Set done state. If no row exists yet, `task_text` is required."""
        done_at = datetime.now(UTC).isoformat() if done else None
        with self._db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM todo_state WHERE recording_id = ? AND todo_index = ?",
                (recording_id, todo_index),
            ).fetchone()
            if existing is None:
                if task_text is None:
                    raise ValueError("task_text is required when no existing row matches")
                conn.execute(
                    """
                    INSERT INTO todo_state (recording_id, todo_index, task_text, done, done_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (recording_id, todo_index, task_text, int(done), done_at),
                )
            else:
                conn.execute(
                    "UPDATE todo_state SET done = ?, done_at = ? WHERE id = ?",
                    (int(done), done_at, existing["id"]),
                )
            conn.commit()

    def list_for_recording(self, recording_id: int) -> list[TodoState]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM todo_state WHERE recording_id = ? ORDER BY todo_index",
                (recording_id,),
            ).fetchall()
        return [_row_to_todo(r) for r in rows]
