"""Tests for TranscriptWindow."""

from __future__ import annotations

import pytest


def test_transcript_window_loads_segments(tmp_path, qapp) -> None:
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
    from teams_transcriber.ui.transcript_window import TranscriptWindow

    db = build_database(tmp_path / "test.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-21T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=10_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    TranscriptRepo(db).append(TranscriptSegment(
        id=None, recording_id=rec.id, start_ms=0, end_ms=1500,
        channel=Channel.ME, text="hello transcript",
    ))

    win = TranscriptWindow(db=db, recording_id=rec.id)
    assert "hello transcript" in win.transcript_view.toPlainText()
    db.close()
