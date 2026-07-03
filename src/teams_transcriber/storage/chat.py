"""Repo for the chat_messages table (schema v5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from teams_transcriber.storage.db import Database


@dataclass(slots=True)
class ChatMessage:
    id: int | None
    recording_id: int
    role: str             # 'user' | 'assistant'
    content: str
    created_at: str


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ChatRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list_for_recording(self, recording_id: int) -> list[ChatMessage]:
        with self._db.connect() as conn:
            cur = conn.execute(
                "SELECT id, recording_id, role, content, created_at "
                "FROM chat_messages WHERE recording_id = ? ORDER BY id",
                (recording_id,),
            )
            rows = cur.fetchall()
        return [
            ChatMessage(
                id=r[0], recording_id=r[1], role=r[2],
                content=r[3], created_at=r[4],
            )
            for r in rows
        ]

    def append(self, recording_id: int, role: str, content: str) -> int:
        with self._db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO chat_messages (recording_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (recording_id, role, content, _now_iso()),
            )
            conn.commit()
        return cur.lastrowid

    def clear(self, recording_id: int) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "DELETE FROM chat_messages WHERE recording_id = ?",
                (recording_id,),
            )
            conn.commit()
