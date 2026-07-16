"""Tests for `audio.importer.import_audio_file`."""

from __future__ import annotations

import wave
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from teams_transcriber.audio.importer import import_audio_file
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    build_database,
)


def _write_mono_wav(path: Path, seconds: float = 1.0, rate: int = 16_000) -> None:
    n = int(seconds * rate)
    t = np.linspace(0, seconds, n, endpoint=False, dtype=np.float32)
    samples = (0.25 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


@pytest.fixture
def env(tmp_path: Path):
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    yield paths, db
    db.close()


def test_import_copies_file_and_creates_recording_row(env, tmp_path: Path) -> None:
    paths, db = env
    src = tmp_path / "external" / "my-meeting.wav"
    src.parent.mkdir(parents=True)
    _write_mono_wav(src, seconds=1.0)

    rid = import_audio_file(src, db=db, paths=paths)
    assert rid > 0

    rec = RecordingRepo(db).get(rid)
    assert rec is not None
    assert rec.status == RecordingStatus.TRANSCRIBING
    assert rec.source == RecordingSource.MANUAL
    assert rec.audio_path is not None
    copied = Path(rec.audio_path)
    assert copied.is_file()
    assert "imported-my-meeting" in copied.name
    assert copied.parent == paths.audio_dir
    assert rec.display_title == "My Meeting"
    assert rec.duration_ms is not None and 800 <= rec.duration_ms <= 1200
    assert src.is_file()   # source untouched


def test_import_handles_filename_collision(env, tmp_path: Path) -> None:
    paths, db = env
    src = tmp_path / "src" / "meet.wav"
    src.parent.mkdir(parents=True)
    _write_mono_wav(src, seconds=0.5)

    rid1 = import_audio_file(src, db=db, paths=paths)
    rid2 = import_audio_file(src, db=db, paths=paths)
    assert rid1 != rid2

    a = RecordingRepo(db).get(rid1)
    b = RecordingRepo(db).get(rid2)
    assert a and b and a.audio_path != b.audio_path
    assert Path(a.audio_path).is_file()
    assert Path(b.audio_path).is_file()


def test_import_missing_file_raises(env, tmp_path: Path) -> None:
    paths, db = env
    with pytest.raises(FileNotFoundError):
        import_audio_file(tmp_path / "nope.wav", db=db, paths=paths)


def test_import_rejects_non_audio_file(env, tmp_path: Path) -> None:
    paths, db = env
    bad = tmp_path / "doc.txt"
    bad.write_text("not audio")
    with pytest.raises(Exception):
        import_audio_file(bad, db=db, paths=paths)
    # No recording row created on failure.
    rows = list(RecordingRepo(db).list_recent(limit=10))
    assert rows == []


def test_import_honors_metadata_overrides(env, tmp_path: Path) -> None:
    paths, db = env
    src = tmp_path / "external" / "REC0001.wav"
    src.parent.mkdir(parents=True)
    _write_mono_wav(src, seconds=1.0)

    when = datetime(2026, 7, 14, 9, 0, 0, tzinfo=UTC)
    rid = import_audio_file(
        src, db=db, paths=paths,
        title="Site walkthrough", started_at_override=when,
    )
    rec = RecordingRepo(db).get(rid)
    assert rec is not None
    assert rec.display_title == "Site walkthrough"
    assert rec.started_at == when.isoformat()


def test_import_without_overrides_still_derives_from_filename(env, tmp_path: Path) -> None:
    """Backward compatibility: omitting title/started_at_override keeps the
    existing filename/mtime-derived behavior."""
    paths, db = env
    src = tmp_path / "external" / "my-meeting.wav"
    src.parent.mkdir(parents=True)
    _write_mono_wav(src, seconds=0.5)

    rid = import_audio_file(src, db=db, paths=paths)
    rec = RecordingRepo(db).get(rid)
    assert rec is not None
    assert rec.display_title == "My Meeting"
