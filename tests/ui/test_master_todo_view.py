import pytest
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    build_database, RecordingRepo, SummaryRepo, TodoStateRepo,
)
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus, Summary, TodoItem,
)
from teams_transcriber.ui.master_todo_view import MasterTodoView


@pytest.fixture
def db(tmp_path, qapp):
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    d = build_database(paths.db_path)
    d.initialize()
    yield d
    d.close()


def _add(db, title, todos):
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-20T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title=title, display_title=title,
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title=title, one_line=None, summary="s",
        key_decisions=[], my_todos=[TodoItem(task=t) for t in todos],
        action_items_others=[], follow_ups=[], topics=[],
        generated_at="2026-05-20T11:00:00+00:00", model_used="m",
    ))
    return rec.id


def test_reload_groups_only_meetings_with_todos(db):
    rid_a = _add(db, "Has Todos", ["one", "two"])
    _add(db, "No Todos", [])
    view = MasterTodoView(db)
    view.reload()
    assert view.group_count() == 1
    assert rid_a in view.group_recording_ids()


def test_go_to_summary_emits(db):
    rid = _add(db, "Meeting", ["x"])
    view = MasterTodoView(db)
    view.reload()
    seen = []
    view.go_to_summary.connect(seen.append)
    view._emit_go_to_summary(rid)
    assert seen == [rid]


def test_toggle_persists_and_emits(db):
    rid = _add(db, "Meeting", ["x"])
    view = MasterTodoView(db)
    view.reload()
    seen = []
    view.todo_toggled.connect(seen.append)
    view._toggle(rid, 0, "x", True)
    states = {s.todo_index: s.done for s in TodoStateRepo(db).list_for_recording(rid)}
    assert states.get(0) is True
    assert seen == [rid]


def test_empty_state(db):
    view = MasterTodoView(db)
    view.reload()
    assert view.group_count() == 0
    assert view.is_empty() is True


def test_master_todos_use_wrapping_rows(db):
    from PySide6.QtWidgets import QCheckBox
    _add(db, "Meeting", ["one", "two"])
    view = MasterTodoView(db)
    view.reload()
    cbs = view._container.findChildren(QCheckBox)
    assert cbs and all(cb.text() == "" for cb in cbs)  # text lives in labels now
