from __future__ import annotations

from pathlib import Path

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    PhoneImportRepo, Recording, RecordingRepo, RecordingSource, RecordingStatus,
    build_database,
)


def _make_recording(db) -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-07-14T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    return rec.id


def test_record_and_lookup_roundtrip(tmp_path: Path):
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    try:
        rid = _make_recording(db)
        repo = PhoneImportRepo(db)
        assert repo.recording_id_for("uid-1") is None
        repo.record("uid-1", rid, "in_person")
        assert repo.recording_id_for("uid-1") == rid
        assert repo.source_for_recordings() == {rid: "in_person"}
    finally:
        db.close()


def test_record_same_uid_twice_is_noop(tmp_path: Path):
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    try:
        rid = _make_recording(db)
        repo = PhoneImportRepo(db)
        repo.record("uid-1", rid, "memo")
        repo.record("uid-1", rid, "memo")   # idempotent, no IntegrityError
        assert repo.recording_id_for("uid-1") == rid
    finally:
        db.close()


def test_ledger_row_cascades_with_recording(tmp_path: Path):
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    try:
        rid = _make_recording(db)
        repo = PhoneImportRepo(db)
        repo.record("uid-1", rid, "teams_call")
        RecordingRepo(db).delete(rid)
        assert repo.recording_id_for("uid-1") is None
    finally:
        db.close()
