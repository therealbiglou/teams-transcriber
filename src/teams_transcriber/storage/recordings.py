"""Repository for the `recordings` table."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import Recording, RecordingSource, RecordingStatus


# Module-level row-mapper (not a @staticmethod) so tests and other modules can call
# without instantiating the repo. The same pattern applies in the other repo modules.
def _row_to_recording(row: sqlite3.Row) -> Recording:
    # row.keys() lets us tolerate older test DBs without the manual_notes column.
    keys = set(row.keys())
    return Recording(
        id=row["id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        source=RecordingSource(row["source"]),
        detected_title=row["detected_title"],
        display_title=row["display_title"],
        audio_path=row["audio_path"],
        audio_deleted_at=row["audio_deleted_at"],
        duration_ms=row["duration_ms"],
        status=RecordingStatus(row["status"]),
        error_message=row["error_message"],
        manual_notes=row["manual_notes"] if "manual_notes" in keys else None,
    )


class RecordingRepo:
    """CRUD for recordings. All methods serialize on the Database lock."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, rec: Recording) -> Recording:
        with self._db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO recordings (
                    started_at, ended_at, source, detected_title, display_title,
                    audio_path, audio_deleted_at, duration_ms, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.started_at,
                    rec.ended_at,
                    rec.source.value,
                    rec.detected_title,
                    rec.display_title,
                    rec.audio_path,
                    rec.audio_deleted_at,
                    rec.duration_ms,
                    rec.status.value,
                    rec.error_message,
                ),
            )
            conn.commit()
            rec.id = cur.lastrowid
            return rec

    def get(self, recording_id: int) -> Recording | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
        return _row_to_recording(row) if row is not None else None

    def list_recent(self, limit: int = 50) -> list[Recording]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recordings ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_recording(r) for r in rows]

    def list_by_status(self, status: RecordingStatus) -> list[Recording]:
        """Return all recordings in the given status, newest first.

        Used at startup to find recordings that crashed mid-pipeline so they can be
        resumed or marked failed.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recordings WHERE status = ? ORDER BY started_at DESC",
                (status.value,),
            ).fetchall()
        return [_row_to_recording(r) for r in rows]

    def update_status(
        self,
        recording_id: int,
        status: RecordingStatus,
        error_message: str | None = None,
    ) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET status = ?, error_message = ? WHERE id = ?",
                (status.value, error_message, recording_id),
            )
            conn.commit()

    def finalize(self, recording_id: int, ended_at: str, duration_ms: int) -> None:
        """Set ended_at and duration_ms. Status is updated separately via update_status()."""
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET ended_at = ?, duration_ms = ? WHERE id = ?",
                (ended_at, duration_ms, recording_id),
            )
            conn.commit()

    def set_display_title(self, recording_id: int, title: str) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET display_title = ? WHERE id = ?",
                (title, recording_id),
            )
            conn.commit()

    def set_audio_path(self, recording_id: int, audio_path: str) -> None:
        """Set the recording's audio_path. Use mark_audio_deleted() to null it instead."""
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET audio_path = ? WHERE id = ?",
                (audio_path, recording_id),
            )
            conn.commit()

    def mark_audio_deleted(self, recording_id: int, deleted_at: str) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET audio_path = NULL, audio_deleted_at = ? WHERE id = ?",
                (deleted_at, recording_id),
            )
            conn.commit()

    def delete(self, recording_id: int) -> None:
        with self._db.connect() as conn:
            conn.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
            conn.commit()

    def set_manual_notes(self, recording_id: int, notes: str | None) -> None:
        """Update the user's manual notes (HTML)."""
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET manual_notes = ? WHERE id = ?",
                (notes, recording_id),
            )
            conn.commit()
