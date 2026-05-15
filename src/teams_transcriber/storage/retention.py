"""Prunes audio files past their retention window.

Transcripts and summaries are kept indefinitely - only the audio file itself is deleted
and the recording row's `audio_path` is nulled (with `audio_deleted_at` set).

Recordings still in progress (status == 'recording') are never pruned regardless of age.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import RecordingStatus


@dataclass(slots=True)
class PruneReport:
    deleted_count: int = 0
    missing_count: int = 0
    skipped_count: int = 0


class AudioRetentionPruner:
    def __init__(
        self,
        db: Database,
        retention_days: int,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if retention_days < 0:
            raise ValueError("retention_days must be >= 0")
        self._db = db
        self._retention_days = retention_days
        self._now = now

    def run(self) -> PruneReport:
        report = PruneReport()
        if self._retention_days == 0:
            return report

        now = self._now()
        cutoff = (now - timedelta(days=self._retention_days)).isoformat()
        now_iso = now.isoformat()

        with self._db.connect() as conn:
            # Rows eligible for pruning: have audio, older than cutoff, not in active recording.
            eligible = conn.execute(
                """
                SELECT id, audio_path FROM recordings
                WHERE audio_path IS NOT NULL
                  AND started_at < ?
                  AND status != ?
                """,
                (cutoff, RecordingStatus.RECORDING.value),
            ).fetchall()

            # Rows we deliberately did NOT touch - newer than cutoff or in active recording -
            # but which still have audio. Reported as "skipped" for visibility.
            skipped = conn.execute(
                """
                SELECT COUNT(*) AS n FROM recordings
                WHERE audio_path IS NOT NULL
                  AND (started_at >= ? OR status = ?)
                """,
                (cutoff, RecordingStatus.RECORDING.value),
            ).fetchone()["n"]
            report.skipped_count = skipped

            for row in eligible:
                path = Path(row["audio_path"])
                if path.exists():
                    try:
                        path.unlink()
                        report.deleted_count += 1
                    except OSError:
                        # Could not delete (locked, permissions, etc.) - leave row alone,
                        # do not null out audio_path so we'll retry next run.
                        report.skipped_count += 1
                        continue
                else:
                    report.missing_count += 1

                conn.execute(
                    """
                    UPDATE recordings
                    SET audio_path = NULL, audio_deleted_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, row["id"]),
                )
            conn.commit()

        return report
