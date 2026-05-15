"""Repository for transcript segments + FTS5 search across transcripts."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import Channel, TranscriptSegment


@dataclass(slots=True)
class SearchHit:
    """A single FTS hit. `snippet` is HTML-free, with matched terms wrapped in `<mark>...</mark>`."""

    recording_id: int
    recording_title: str | None
    segment_id: int
    start_ms: int
    end_ms: int
    channel: Channel
    snippet: str


# Module-level row-mapper, mirroring the convention from recordings.py.
def _row_to_segment(row: sqlite3.Row) -> TranscriptSegment:
    return TranscriptSegment(
        id=row["id"],
        recording_id=row["recording_id"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        channel=Channel(row["channel"]),
        text=row["text"],
    )


def _escape_fts_query(query: str) -> str:
    """Wrap each whitespace-separated token in quotes so FTS treats them as literals.

    FTS5 query syntax interprets characters like `"`, `*`, `:`, `-`, `(`, `)` specially.
    For a search UI we want all user input to be treated as plain text. We escape any
    internal `"` by doubling it, then wrap each token in double quotes.
    """
    tokens = query.split()
    if not tokens:
        return ""
    quoted = []
    for tok in tokens:
        escaped = tok.replace('"', '""')
        quoted.append(f'"{escaped}"')
    return " ".join(quoted)


class TranscriptRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def append(self, seg: TranscriptSegment) -> TranscriptSegment:
        with self._db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO transcript_segments
                    (recording_id, start_ms, end_ms, channel, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (seg.recording_id, seg.start_ms, seg.end_ms, seg.channel.value, seg.text),
            )
            conn.commit()
            seg.id = cur.lastrowid
            return seg

    def append_many(self, segs: Iterable[TranscriptSegment]) -> None:
        rows = [
            (s.recording_id, s.start_ms, s.end_ms, s.channel.value, s.text) for s in segs
        ]
        if not rows:
            return
        with self._db.connect() as conn:
            conn.executemany(
                """
                INSERT INTO transcript_segments
                    (recording_id, start_ms, end_ms, channel, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def list_for_recording(self, recording_id: int) -> list[TranscriptSegment]:
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM transcript_segments
                WHERE recording_id = ?
                ORDER BY start_ms ASC
                """,
                (recording_id,),
            ).fetchall()
        return [_row_to_segment(r) for r in rows]

    def search(self, query: str, limit: int = 50) -> list[SearchHit]:
        """Full-text search over transcript segments. Returns highlighted snippets."""
        match = _escape_fts_query(query)
        if not match:
            return []
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    ts.id              AS segment_id,
                    ts.recording_id    AS recording_id,
                    ts.start_ms        AS start_ms,
                    ts.end_ms          AS end_ms,
                    ts.channel         AS channel,
                    r.display_title    AS recording_title,
                    snippet(transcript_fts, 0, '<mark>', '</mark>', '…', 16) AS snippet
                FROM transcript_fts
                JOIN transcript_segments ts ON ts.id = transcript_fts.rowid
                JOIN recordings           r ON r.id  = ts.recording_id
                WHERE transcript_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        return [
            SearchHit(
                recording_id=row["recording_id"],
                recording_title=row["recording_title"],
                segment_id=row["segment_id"],
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
                channel=Channel(row["channel"]),
                snippet=row["snippet"],
            )
            for row in rows
        ]
