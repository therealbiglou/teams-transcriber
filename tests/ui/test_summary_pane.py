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


def test_copy_markdown_uses_done_state(qapp, db_with_summary) -> None:
    """_copy_markdown should include [x] for a todo marked done via TodoStateRepo."""
    db, rec_id = db_with_summary
    repo = SummaryRepo(db)
    summary = repo.get(rec_id)
    assert summary is not None
    rec = RecordingRepo(db).get(rec_id)
    assert rec is not None

    # Mark todo index 0 done
    TodoStateRepo(db).upsert(rec_id, 0, "Do A", True)

    pane = SummaryPane(db)
    pane._copy_markdown(summary, rec)

    from PySide6.QtGui import QGuiApplication
    text = QGuiApplication.clipboard().text()
    assert "- [x] " in text, f"Expected '- [x] ' in clipboard text:\n{text}"
    assert "- [ ] " in text, f"Expected '- [ ] ' for undone todo in clipboard text:\n{text}"


def test_todo_state_changed_signal_emitted_on_toggle(qapp, qtbot, db_with_summary) -> None:
    """Toggling a todo checkbox emits todo_state_changed with the recording_id."""
    db, rec_id = db_with_summary
    pane = SummaryPane(db)

    received: list[int] = []
    pane.todo_state_changed.connect(received.append)

    pane.show_recording(rec_id)
    from PySide6.QtWidgets import QCheckBox
    cbs = pane.findChildren(QCheckBox)
    assert len(cbs) >= 1, "Expected at least one todo checkbox"
    # Toggle the first checkbox (currently unchecked → checked)
    cbs[0].setChecked(True)
    assert received == [rec_id]


def test_all_non_header_labels_are_selectable(tmp_path, qapp) -> None:
    """Every visible QLabel — including section/card headers — must be text-selectable.

    Covers: title, meta, summary body, notes body, action-items-others body,
    key-decisions body, follow-ups body, AND the bold card-title headers
    (Summary, My notes, Topics, etc.). Also covers the two bare placeholder
    labels ("Recording not found." and "No summary yet for this recording.").
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    from teams_transcriber.storage import (
        ActionItemOther,
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
        Summary,
        SummaryRepo,
        TodoItem,
        build_database,
    )
    from teams_transcriber.ui.summary_pane import SummaryPane

    db = build_database(tmp_path / "selectable.db")
    db.initialize()
    repo = RecordingRepo(db)
    rec = repo.create(Recording(
        id=None, started_at="2026-05-21T10:00:00+00:00",
        ended_at="2026-05-21T11:00:00+00:00",
        source=RecordingSource.MANUAL,
        detected_title="t", display_title="Selectable test",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    # Set manual_notes so the notes label is rendered.
    repo.set_manual_notes(rec.id, "<p>My handwritten note</p>")

    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id,
        title="Selectable test", one_line="x",
        summary="Summary body text",
        my_todos=[TodoItem(task="Do A")],
        action_items_others=[ActionItemOther(who="Alice", task="Write spec")],
        key_decisions=["Ship in July"],
        follow_ups=["Revisit pricing"],
        topics=["billing"],
        model_used="claude-sonnet-4-6",
        generated_at="2026-05-21T10:00:00+00:00",
    ))

    pane = SummaryPane(db)
    pane.show_recording(rec.id)

    all_labels = pane.findChildren(QLabel)
    assert all_labels, "Expected at least some labels"

    non_selectable = [
        lbl.text() for lbl in all_labels
        if not (lbl.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
    ]
    assert non_selectable == [], (
        f"These labels are NOT selectable: {non_selectable}"
    )

    # --- Also verify the two placeholder paths ---
    # "Recording not found." placeholder
    db2 = build_database(tmp_path / "selectable2.db")
    db2.initialize()
    pane2 = SummaryPane(db2)
    pane2.show_recording(9999)  # non-existent id
    not_found_labels = [
        lbl for lbl in pane2.findChildren(QLabel)
        if "Recording not found" in lbl.text()
    ]
    assert not_found_labels, "Expected a 'Recording not found.' label"
    for lbl in not_found_labels:
        assert lbl.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse, (
            "'Recording not found.' label is not selectable"
        )

    # "No summary yet" placeholder
    rec3 = RecordingRepo(db2).create(Recording(
        id=None, started_at="2026-05-21T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=60_000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    pane3 = SummaryPane(db2)
    pane3.show_recording(rec3.id)
    no_summary_labels = [
        lbl for lbl in pane3.findChildren(QLabel)
        if "No summary yet" in lbl.text()
    ]
    assert no_summary_labels, "Expected a 'No summary yet' label"
    for lbl in no_summary_labels:
        assert lbl.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse, (
            "'No summary yet' label is not selectable"
        )

    db.close()
    db2.close()
