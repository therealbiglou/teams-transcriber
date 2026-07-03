"""Import an external transcript file as a recording.

Reads a UTF-8 text file (.txt / .md / .vtt / .srt — the last two are accepted
as plain text for v1; their inline timestamps don't confuse the summarizer)
and creates a Recording row with **no audio file**, plus a single
TranscriptSegment containing the file's contents.

Downstream, `Pipeline.import_transcript_file` submits the recording to the
existing post-processing executor. `Transcriber.transcribe()` already has a
"skip Whisper if existing segments cover the duration" branch, so we set
the recording's `duration_ms` equal to the segment's `end_ms` (a sentinel
value of 1 ms is enough) and the coverage check (≥ 95 %) passes
immediately — the transcriber publishes `TranscriptionComplete` without
touching audio, the summarizer fires, and the rest of the pipeline
(Wrike sync etc.) flows as for any normal meeting.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    Channel,
    Database,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    TranscriptRepo,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

TRANSCRIPT_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md", ".vtt", ".srt"})


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (cleaned or "imported")[:40]


def _display_title(stem: str) -> str:
    """Turn a filename stem like 'q3-planning-notes' into 'Q3 Planning Notes'."""
    cleaned = re.sub(r"[-_]+", " ", stem).strip()
    return cleaned.title() or "Imported transcript"


def is_transcript_file(path: Path) -> bool:
    """True if the file's extension is one of TRANSCRIPT_EXTENSIONS."""
    return path.suffix.lower() in TRANSCRIPT_EXTENSIONS


def import_transcript_file(src: Path, *, db: Database, paths: AppPaths) -> int:
    """Create a Recording from an external transcript file.

    Returns the new recording's id. Raises FileNotFoundError if `src` is
    missing, or ValueError if the file is empty / unreadable as UTF-8.

    No audio is associated. The full file contents become a single
    TranscriptSegment tagged Channel.ME. Caller submits to the pipeline
    (see Pipeline.import_transcript_file).
    """
    if not src.is_file():
        raise FileNotFoundError(str(src))
    try:
        text = src.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # Fall back to a permissive decode so a Word "smart quote" or BOM
        # doesn't sink the import.
        logger.warning("transcript %s not strict utf-8: %s; retrying with replacement", src, exc)
        text = src.read_text(encoding="utf-8", errors="replace")
    stripped = text.strip()
    if not stripped:
        raise ValueError(f"transcript file is empty: {src}")

    try:
        started_at = datetime.fromtimestamp(src.stat().st_mtime, tz=UTC)
    except OSError:
        started_at = datetime.now(UTC)

    title = _display_title(src.stem)
    # duration_ms = 1 + segment end_ms = 1 is enough for Transcriber's
    # coverage gate (covered/duration = 1.0) to skip Whisper entirely.
    rec = RecordingRepo(db).create(Recording(
        id=None,
        started_at=started_at.isoformat(),
        ended_at=None,
        source=RecordingSource.MANUAL,
        detected_title=title,
        display_title=title,
        audio_path=None,
        audio_deleted_at=None,
        duration_ms=1,
        status=RecordingStatus.TRANSCRIBING,
        error_message=None,
    ))
    assert rec.id is not None
    TranscriptRepo(db).append_many([
        TranscriptSegment(
            id=None, recording_id=rec.id,
            start_ms=0, end_ms=1, channel=Channel.ME, text=stripped,
        ),
    ])
    logger.info(
        "imported transcript %s -> recording %d (%d chars, slug=%s)",
        src, rec.id, len(stripped), _slug(src.stem),
    )
    return rec.id
