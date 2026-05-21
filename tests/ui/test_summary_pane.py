from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from teams_transcriber.storage import (
    ActionItemOther,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    Summary,
    SummaryRepo,
    TodoItem,
    TodoStateRepo,
    build_database,
)
from teams_transcriber.ui.summary_pane import SummaryPane


@pytest.fixture
def db_with_summary(tmp_path: Path):
    db = build_database(tmp_path / "tt.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-15T10:00:00+00:00",
        ended_at="2026-05-15T10:30:00+00:00",
        source=RecordingSource.TEAMS, detected_title="X | Microsoft Teams",
        display_title="Q2 sync", audio_path=None, audio_deleted_at=None,
        duration_ms=30 * 60 * 1000, status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id,
        title="Q2 sync",
        one_line="Aligned on x.",
        summary="Discussed x.",
        key_decisions=["Ship in July"],
        my_todos=[TodoItem(task="Do A"), TodoItem(task="Do B")],
        action_items_others=[ActionItemOther(who="Sarah", task="Migration doc")],
        follow_ups=["Revisit pricing"],
        topics=["billing"],
        generated_at=datetime.now(UTC).isoformat(),
        model_used="claude-sonnet-4-6",
    ))
    yield db, rec.id
    db.close()


def test_pane_shows_summary(qapp, qtbot, db_with_summary) -> None:
    db, rec_id = db_with_summary
    pane = SummaryPane(db)
    pane.show_recording(rec_id)
    from PySide6.QtWidgets import QCheckBox
    todos = pane.findChildren(QCheckBox)
    assert len(todos) == 2
    assert "Do A" in todos[0].text()


def test_todo_toggle_persists(qapp, qtbot, db_with_summary) -> None:
    db, rec_id = db_with_summary
    pane = SummaryPane(db)
    pane.show_recording(rec_id)
    from PySide6.QtWidgets import QCheckBox
    cbs = pane.findChildren(QCheckBox)
    cbs[0].setChecked(True)
    states = TodoStateRepo(db).list_for_recording(rec_id)
    assert any(s.done and s.task_text == "Do A" for s in states)


def test_summary_pane_has_view_transcript_button(tmp_path, qapp) -> None:
    """The summary pane shows a 'View transcript' button (replaces inline card)."""
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        Summary,
        SummaryRepo,
        build_database,
    )
    from teams_transcriber.ui.summary_pane import SummaryPane
    from PySide6.QtWidgets import QPushButton

    db = build_database(tmp_path / "test.db")
    db.initialize()

    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-21T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t", audio_path=None,
        audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id,
        title="t", one_line="line", summary="body",
        my_todos=[], action_items_others=[], key_decisions=[],
        follow_ups=[], topics=[], model_used="m",
        generated_at="2026-05-21T10:00:00+00:00",
    ))

    received: list[int] = []
    pane = SummaryPane(db)
    pane.transcript_requested.connect(received.append)
    pane.show_recording(rec.id)

    btns = [b for b in pane.findChildren(QPushButton) if b.text() == "View transcript"]
    assert len(btns) == 1
    btns[0].click()
    assert received == [rec.id]
    db.close()


def test_summary_pane_shows_error_for_failed_recording(tmp_path, qapp) -> None:
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        build_database,
    )
    from teams_transcriber.ui.summary_pane import SummaryPane
    from PySide6.QtWidgets import QLabel

    db = build_database(tmp_path / "test.db")
    db.initialize()

    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-20T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=10_000,
        status=RecordingStatus.SUMMARY_FAILED,
        error_message="transcript is empty",
    ))

    pane = SummaryPane(db)
    pane.show_recording(rec.id)

    label_texts = [c.text() for c in pane.findChildren(QLabel)]
    assert any("transcript is empty" in t for t in label_texts)
    assert any("Summarization failed" in t for t in label_texts)
    db.close()


def test_summary_pane_failed_card_has_retry_button_for_recoverable_statuses(tmp_path, qapp) -> None:
    """Failed recordings with TRANSCRIPTION_FAILED / SUMMARY_FAILED get a Retry button."""
    from PySide6.QtWidgets import QPushButton
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        build_database,
    )
    from teams_transcriber.ui.summary_pane import SummaryPane

    db = build_database(tmp_path / "test.db")
    db.initialize()

    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-21T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=10_000,
        status=RecordingStatus.SUMMARY_FAILED,
        error_message="transcript is empty",
    ))

    received: list[int] = []
    pane = SummaryPane(db)
    pane.retry_requested.connect(received.append)
    pane.show_recording(rec.id)

    # Find the Retry button.
    btns = [b for b in pane.findChildren(QPushButton) if b.text() == "Retry"]
    assert len(btns) == 1
    btns[0].click()
    assert received == [rec.id]
    db.close()


def test_summary_pane_labels_are_selectable(tmp_path, qapp) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        Summary,
        SummaryRepo,
        build_database,
    )
    from teams_transcriber.ui.summary_pane import SummaryPane

    db = build_database(tmp_path / "test.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-21T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="Test title", one_line="x",
        summary="Body text here", my_todos=[], action_items_others=[],
        key_decisions=["decision A"], follow_ups=[], topics=[],
        model_used="m", generated_at="2026-05-21T10:00:00+00:00",
    ))
    pane = SummaryPane(db)
    pane.show_recording(rec.id)

    # At least the title and summary body should be text-selectable.
    selectable_count = 0
    for label in pane.findChildren(QLabel):
        flags = label.textInteractionFlags()
        if flags & Qt.TextInteractionFlag.TextSelectableByMouse:
            selectable_count += 1
    assert selectable_count >= 2  # title + summary at minimum
    db.close()


def test_summary_pane_failed_card_has_delete_button(tmp_path, qapp) -> None:
    from PySide6.QtWidgets import QPushButton
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        build_database,
    )
    from teams_transcriber.ui.summary_pane import SummaryPane

    db = build_database(tmp_path / "test.db")
    db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-21T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=10_000,
        status=RecordingStatus.SUMMARY_FAILED,
        error_message="boom",
    ))
    received: list[int] = []
    pane = SummaryPane(db)
    pane.delete_requested.connect(received.append)
    pane.show_recording(rec.id)
    btns = [b for b in pane.findChildren(QPushButton) if b.text() == "Delete"]
    assert len(btns) == 1
    btns[0].click()
    assert received == [rec.id]
    db.close()


def test_summary_pane_no_retry_for_recording_failed(tmp_path, qapp) -> None:
    """RECORDING_FAILED has no audio to retry from — no Retry button."""
    from PySide6.QtWidgets import QPushButton
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        build_database,
    )
    from teams_transcriber.ui.summary_pane import SummaryPane

    db = build_database(tmp_path / "test.db")
    db.initialize()

    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-21T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=10_000,
        status=RecordingStatus.RECORDING_FAILED,
        error_message="audio device disappeared",
    ))

    pane = SummaryPane(db)
    pane.show_recording(rec.id)

    btns = [b for b in pane.findChildren(QPushButton) if b.text() == "Retry"]
    assert len(btns) == 0
    db.close()
