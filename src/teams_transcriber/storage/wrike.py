"""Repos for the wrike_sync and wrike_tasks tables (schema v4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from teams_transcriber.storage.db import Database


@dataclass(slots=True)
class WrikeSyncRow:
    recording_id: int
    folder_id: str | None
    status: str
    last_attempted_at: str | None
    error_message: str | None


@dataclass(slots=True)
class WrikeTaskRow:
    id: int | None
    recording_id: int
    kind: str
    todo_index: int
    wrike_task_id: str  # carries the comment id when format == "comment"
    wrike_folder_id: str
    created_at: str
    last_synced_done: bool
    format: str = "task"  # "task" | "comment"
    assignee_id: str | None = None


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


class WrikeSyncRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, recording_id: int) -> WrikeSyncRow | None:
        with self._db.connect() as conn:
            cur = conn.execute(
                "SELECT recording_id, folder_id, status, last_attempted_at, "
                "error_message FROM wrike_sync WHERE recording_id = ?",
                (recording_id,),
            )
            row = cur.fetchone()
        return None if row is None else WrikeSyncRow(*row)

    def upsert(
        self,
        recording_id: int,
        *,
        status: str,
        folder_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO wrike_sync (recording_id, folder_id, status, "
                "last_attempted_at, error_message) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(recording_id) DO UPDATE SET folder_id=excluded.folder_id, "
                "status=excluded.status, last_attempted_at=excluded.last_attempted_at, "
                "error_message=excluded.error_message",
                (recording_id, folder_id, status, _now_utc(), error_message),
            )
            conn.commit()

    def update(
        self,
        recording_id: int,
        *,
        status: str,
        folder_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.upsert(
            recording_id, status=status,
            folder_id=folder_id, error_message=error_message,
        )

    def list_pending_or_failed(self) -> list[WrikeSyncRow]:
        with self._db.connect() as conn:
            cur = conn.execute(
                "SELECT recording_id, folder_id, status, last_attempted_at, "
                "error_message FROM wrike_sync WHERE status IN ('pending', 'failed') "
                "ORDER BY recording_id"
            )
            return [WrikeSyncRow(*r) for r in cur.fetchall()]


class WrikeTaskRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, row: WrikeTaskRow) -> int:
        with self._db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO wrike_tasks (recording_id, kind, todo_index, "
                "wrike_task_id, wrike_folder_id, created_at, last_synced_done, "
                "format, assignee_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row.recording_id, row.kind, row.todo_index, row.wrike_task_id,
                 row.wrike_folder_id, row.created_at, 1 if row.last_synced_done else 0,
                 row.format, row.assignee_id),
            )
            conn.commit()
            return cur.lastrowid

    def get(self, recording_id: int, kind: str, todo_index: int) -> WrikeTaskRow | None:
        with self._db.connect() as conn:
            cur = conn.execute(
                "SELECT id, recording_id, kind, todo_index, wrike_task_id, "
                "wrike_folder_id, created_at, last_synced_done, format, assignee_id "
                "FROM wrike_tasks WHERE recording_id=? AND kind=? AND todo_index=?",
                (recording_id, kind, todo_index),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return WrikeTaskRow(
            id=row[0], recording_id=row[1], kind=row[2], todo_index=row[3],
            wrike_task_id=row[4], wrike_folder_id=row[5], created_at=row[6],
            last_synced_done=bool(row[7]), format=row[8], assignee_id=row[9],
        )

    def list_for_recording(self, recording_id: int) -> list[WrikeTaskRow]:
        with self._db.connect() as conn:
            cur = conn.execute(
                "SELECT id, recording_id, kind, todo_index, wrike_task_id, "
                "wrike_folder_id, created_at, last_synced_done, format, assignee_id "
                "FROM wrike_tasks WHERE recording_id=? ORDER BY kind, todo_index",
                (recording_id,),
            )
            return [
                WrikeTaskRow(
                    id=r[0], recording_id=r[1], kind=r[2], todo_index=r[3],
                    wrike_task_id=r[4], wrike_folder_id=r[5], created_at=r[6],
                    last_synced_done=bool(r[7]), format=r[8], assignee_id=r[9],
                )
                for r in cur.fetchall()
            ]

    def set_last_synced_done(
        self, recording_id: int, kind: str, todo_index: int, done: bool,
    ) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE wrike_tasks SET last_synced_done=? "
                "WHERE recording_id=? AND kind=? AND todo_index=?",
                (1 if done else 0, recording_id, kind, todo_index),
            )
            conn.commit()
