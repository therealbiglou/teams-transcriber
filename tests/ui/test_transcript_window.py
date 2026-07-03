"""Tests for TranscriptWindow."""

from __future__ import annotations

import pytest


def _build_db_with_recording(tmp_path):
    """Helper: build a db, insert one recording + one segment, return (db, rec)."""
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
    return db, rec


def test_transcript_window_loads_segments(tmp_path, qapp) -> None:
    from teams_transcriber.ui.transcript_window import TranscriptWindow

    db, rec = _build_db_with_recording(tmp_path)
    win = TranscriptWindow(db=db, recording_id=rec.id)
    assert "hello transcript" in win.transcript_view.toPlainText()
    db.close()


def test_transcript_window_movable_resizable(tmp_path, qapp) -> None:
    from teams_transcriber.ui.transcript_window import TranscriptWindow
    from teams_transcriber.ui.frameless import FramelessWindowMixin
    from PySide6.QtCore import QPoint, Qt

    db, rec = _build_db_with_recording(tmp_path)
    win = TranscriptWindow(db=db, recording_id=rec.id)
    win.resize(720, 600)

    assert isinstance(win, FramelessWindowMixin)
    assert win._edge_at(QPoint(2, 2)) == (Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
    assert win._title_bar.close_btn is not None
    assert win._title_bar.maximize_btn is not None

    db.close()


def test_transcript_window_emits_closed(tmp_path, qapp) -> None:
    from teams_transcriber.ui.transcript_window import TranscriptWindow

    db, rec = _build_db_with_recording(tmp_path)
    win = TranscriptWindow(db=db, recording_id=rec.id)
    received: list[int] = []
    win.closed.connect(received.append)
    win.close()
    assert received == [win._recording_id]
    db.close()
