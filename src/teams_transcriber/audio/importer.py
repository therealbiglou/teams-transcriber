"""Import an external audio file as a recording row.

Copies the source file into the app's audio directory (so retention pruning,
paths, and naming stay consistent with native recordings), creates a Recording
row at status TRANSCRIBING, and returns the new recording id. The caller is
responsible for triggering post-processing — `Pipeline.import_audio_file`
wraps this with the executor submit so the UI has a one-call API.

`source` is reused as `MANUAL` (avoids a CHECK-constraint schema migration);
the import provenance is visible from the filename slug (`imported-<stem>`).
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from teams_transcriber.audio.splitter import probe_audio
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    Database,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
)

logger = logging.getLogger(__name__)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (cleaned or "imported")[:40]


def _display_title(stem: str) -> str:
    """Turn a filename stem like 'my-meeting-notes' into 'My Meeting Notes'."""
    cleaned = re.sub(r"[-_]+", " ", stem).strip()
    return cleaned.title() or "Imported audio"


def import_audio_file(
    src: Path,
    *,
    db: Database,
    paths: AppPaths,
    title: str | None = None,
    started_at_override: datetime | None = None,
) -> int:
    """Copy `src` into the audio dir and create a Recording row.

    Returns the new recording's id. Raises FileNotFoundError if `src` is
    missing, or ValueError (from `probe_audio`) if the file has no audio stream.

    `title` and `started_at_override` let a caller with real metadata (e.g. a
    phone-recording sidecar) skip the filename/mtime-derived defaults. Both
    are optional and backward compatible with the plain filename-import path.

    Caller submits to the pipeline (see Pipeline.import_audio_file for the
    end-to-end wrapper).
    """
    if not src.is_file():
        raise FileNotFoundError(str(src))

    # Probe first so a bad file (wrong type, no audio stream) fails before we
    # copy 100 MB of nothing into the audio dir.
    channels, duration_ms = probe_audio(src)

    if started_at_override is not None:
        started_at = started_at_override
    else:
        try:
            started_at = datetime.fromtimestamp(src.stat().st_mtime, tz=UTC)
        except OSError:
            started_at = datetime.now(UTC)

    paths.audio_dir.mkdir(parents=True, exist_ok=True)
    base = started_at.strftime("%Y-%m-%d_%H%M%S")
    slug_source = title if title else src.stem
    slug = _slug(slug_source)
    ext = src.suffix.lower() or ".opus"
    candidate = paths.audio_dir / f"{base}_imported-{slug}{ext}"
    suffix = 1
    while candidate.exists():
        candidate = paths.audio_dir / f"{base}_imported-{slug}-{suffix}{ext}"
        suffix += 1

    shutil.copy2(str(src), str(candidate))

    display = title if title else _display_title(src.stem)
    rec = RecordingRepo(db).create(Recording(
        id=None,
        started_at=started_at.isoformat(),
        ended_at=None,
        source=RecordingSource.MANUAL,
        detected_title=display,
        display_title=display,
        audio_path=str(candidate),
        audio_deleted_at=None,
        duration_ms=duration_ms or None,
        status=RecordingStatus.TRANSCRIBING,
        error_message=None,
    ))
    assert rec.id is not None
    logger.info(
        "imported %s -> recording %d (%d ms, %d channels) at %s",
        src, rec.id, duration_ms, channels, candidate,
    )
    return rec.id
