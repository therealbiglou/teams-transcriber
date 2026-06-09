import pytest

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import build_database
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.chat import ChatMessage, ChatRepo


@pytest.fixture
def db_with_recording(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    yield db, rec.id
    db.close()


def test_append_and_list_in_insertion_order(db_with_recording):
    db, rid = db_with_recording
    repo = ChatRepo(db)
    repo.append(rid, "user", "what was decided?")
    repo.append(rid, "assistant", "Ship Friday.")
    repo.append(rid, "user", "and who?")
    msgs = repo.list_for_recording(rid)
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert [m.content for m in msgs] == ["what was decided?", "Ship Friday.", "and who?"]


def test_list_empty_for_unknown_recording(db_with_recording):
    db, _ = db_with_recording
    assert ChatRepo(db).list_for_recording(999_999) == []


def test_clear_removes_all_messages_for_recording(db_with_recording):
    db, rid = db_with_recording
    repo = ChatRepo(db)
    repo.append(rid, "user", "x")
    repo.append(rid, "assistant", "y")
    repo.clear(rid)
    assert repo.list_for_recording(rid) == []


def test_clear_only_affects_its_recording(db_with_recording):
    db, rid = db_with_recording
    rid2 = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-09T11:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t2", display_title="t2",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    )).id
    assert rid2 is not None
    repo = ChatRepo(db)
    repo.append(rid, "user", "a")
    repo.append(rid2, "user", "b")
    repo.clear(rid)
    rest = repo.list_for_recording(rid2)
    assert [m.content for m in rest] == ["b"]
