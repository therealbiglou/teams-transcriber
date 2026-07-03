"""Tests for `transcript_importer.import_transcript_file`."""

from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    Channel,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    TranscriptRepo,
    build_database,
)
from teams_transcriber.transcript_importer import (
    TRANSCRIPT_EXTENSIONS,
    import_transcript_file,
    is_transcript_file,
)


@pytest.fixture
def env(tmp_path: Path):
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    yield paths, db
    db.close()


def test_is_transcript_file_recognizes_text_formats(tmp_path):
    for ext in (".txt", ".md", ".vtt", ".srt", ".TXT", ".Md"):
        assert is_transcript_file(tmp_path / f"x{ext}")


def test_is_transcript_file_rejects_audio(tmp_path):
    for ext in (".opus", ".wav", ".mp3", ".m4a"):
        assert not is_transcript_file(tmp_path / f"x{ext}")


def test_import_creates_recording_and_single_segment(env, tmp_path):
    paths, db = env
    src = tmp_path / "src" / "Potter Sync.txt"
    src.parent.mkdir()
    src.write_text(
        "Brian and Jennifer discussed the booth at House of Blues. "
        "Decision: ship Friday. Action: Brian to email floor plan.",
        encoding="utf-8",
    )

    rid = import_transcript_file(src, db=db, paths=paths)
    assert rid > 0

    rec = RecordingRepo(db).get(rid)
    assert rec is not None
    assert rec.status == RecordingStatus.TRANSCRIBING
    assert rec.source == RecordingSource.MANUAL
    assert rec.audio_path is None
    assert rec.display_title == "Potter Sync"
    assert rec.duration_ms == 1

    segs = TranscriptRepo(db).list_for_recording(rid)
    assert len(segs) == 1
    seg = segs[0]
    assert seg.channel == Channel.ME
    assert seg.start_ms == 0 and seg.end_ms == 1
    assert "House of Blues" in seg.text
    # Source untouched.
    assert src.is_file()


def test_import_handles_non_strict_utf8(env, tmp_path):
    paths, db = env
    src = tmp_path / "smart quotes.txt"
    # latin-1 byte that's not valid utf-8
    src.write_bytes(b"\xa3 Pound notes")

    rid = import_transcript_file(src, db=db, paths=paths)
    seg = TranscriptRepo(db).list_for_recording(rid)[0]
    # We use errors="replace" so the bad byte becomes U+FFFD; the rest survives.
    assert "Pound notes" in seg.text


def test_import_rejects_empty_file(env, tmp_path):
    paths, db = env
    src = tmp_path / "blank.txt"
    src.write_text("   \n\n  ", encoding="utf-8")
    with pytest.raises(ValueError):
        import_transcript_file(src, db=db, paths=paths)
    # No row created.
    assert list(RecordingRepo(db).list_recent(limit=10)) == []


def test_import_rejects_missing_file(env, tmp_path):
    paths, db = env
    with pytest.raises(FileNotFoundError):
        import_transcript_file(tmp_path / "nope.txt", db=db, paths=paths)


def test_pipeline_wrapper_creates_row_and_submits(tmp_path, monkeypatch):
    """Pipeline.import_transcript_file = importer + executor submit."""
    from collections.abc import Callable

    from teams_transcriber.audio.source import FakeAudioSource     # noqa: F401
    from teams_transcriber.config import load_settings
    from teams_transcriber.events import EventBus
    from teams_transcriber.pipeline import Pipeline

    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    settings = load_settings(paths)

    class _NoopTranscriber:
        def transcribe(self, rid: int) -> None: pass

    class _NoopSummarizer:
        def summarize(self, rid: int, *, api_key) -> None: pass

    pipe = Pipeline(
        bus=EventBus(), db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: None,    # type: ignore[arg-type]
        meeting_watcher=None,
        transcriber=_NoopTranscriber(),       # type: ignore[arg-type]
        summarizer=_NoopSummarizer(),         # type: ignore[arg-type]
    )
    submitted: list[int] = []
    monkeypatch.setattr(pipe, "_submit_post_processing", lambda rid: submitted.append(rid))

    src = tmp_path / "external" / "notes.md"
    src.parent.mkdir()
    src.write_text("# Heading\n\nbody text", encoding="utf-8")
    rid = pipe.import_transcript_file(str(src))

    assert rid > 0 and submitted == [rid]
    rec = RecordingRepo(db).get(rid)
    assert rec is not None and rec.status == RecordingStatus.TRANSCRIBING
    assert rec.audio_path is None
    seg = TranscriptRepo(db).list_for_recording(rid)[0]
    assert "body text" in seg.text
    db.close()
