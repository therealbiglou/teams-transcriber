from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.storage import (
    Channel,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    TranscriptRepo,
    TranscriptSegment,
    build_database,
)
from teams_transcriber.ui.transcript_view import TranscriptView


@pytest.fixture
def db_with_transcript(tmp_path: Path):
    db = build_database(tmp_path / "tt.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-15T10:00:00+00:00",
        ended_at="2026-05-15T10:30:00+00:00",
        source=RecordingSource.TEAMS, detected_title="X",
        display_title=None, audio_path=None, audio_deleted_at=None,
        duration_ms=30 * 60 * 1000, status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    TranscriptRepo(db).append_many([
        TranscriptSegment(None, rec.id, 0, 2000, Channel.ME, "Hello"),
        TranscriptSegment(None, rec.id, 2000, 4000, Channel.OTHERS, "Hi back"),
    ])
    yield db, rec.id
    db.close()


def test_transcript_renders_segments(qapp, qtbot, db_with_transcript) -> None:
    db, rec_id = db_with_transcript
    view = TranscriptView(db)
    view.show_recording(rec_id)
    from PySide6.QtWidgets import QLabel
    labels = [lbl.text() for lbl in view.findChildren(QLabel)]
    assert any("Hello" in t for t in labels)
    assert any("Hi back" in t for t in labels)
    assert any(t == "ME" for t in labels)
    assert any(t == "OTHER" for t in labels)
