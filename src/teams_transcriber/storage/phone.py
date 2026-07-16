"""Repo for phone_imports — maps Android-app recording UIDs to recording ids.

The ledger makes phone sync idempotent (a re-pulled outbox file whose uid is
already recorded is skipped) and carries the phone-side source
(teams_call | in_person | memo), which recordings.source cannot hold.
"""

from __future__ import annotations

from datetime import UTC, datetime

from teams_transcriber.storage.db import Database


class PhoneImportRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, uid: str, recording_id: int, source: str) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO phone_imports (uid, recording_id, source, imported_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(uid) DO NOTHING",
                (uid, recording_id, source, datetime.now(UTC).isoformat()),
            )
            conn.commit()

    def recording_id_for(self, uid: str) -> int | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT recording_id FROM phone_imports WHERE uid = ?", (uid,),
            ).fetchone()
        return row[0] if row is not None else None

    def source_for_recordings(self) -> dict[int, str]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT recording_id, source FROM phone_imports",
            ).fetchall()
        return {r[0]: r[1] for r in rows}
