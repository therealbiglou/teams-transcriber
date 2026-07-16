"""Tests for the phone-sync engine: pull -> import -> toggles -> export -> ack.

Uses `build_database` (full migration set), not the plain `db` conftest
fixture, which only applies schema v1 -- phone_imports (v7) is required here.
Seeding mirrors tests/storage/test_phone_imports.py and
tests/phone_sync/test_library_export.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from teams_transcriber.phone_sync.sync import run_sync
from teams_transcriber.phone_sync.transport import LocalDirTransport
from teams_transcriber.storage import (
    PhoneImportRepo,
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


def _make_db(tmp_path: Path):
    db = build_database(tmp_path / "tt.db")
    db.initialize()
    return db


def phone(tmp_path: Path) -> LocalDirTransport:
    return LocalDirTransport(tmp_path / "phone")


def seed_outbox(
    t: LocalDirTransport,
    uid: str,
    title: str = "T",
    source: str = "memo",
    started_at: str = "2026-07-14T10:00:00+00:00",
    audio_text: str = "fake-audio-bytes",
) -> None:
    t.push_text(audio_text, f"outbox/rec_{uid}.m4a")
    t.push_text(
        json.dumps({
            "uid": uid, "title": title, "source": source, "started_at": started_at,
        }),
        f"outbox/rec_{uid}.json",
    )


def fake_import(db):
    calls: list[tuple[str, str | None, object]] = []

    def _import(src_path: str, *, title: str | None, started_at) -> int:
        calls.append((src_path, title, started_at))
        rec = RecordingRepo(db).create(Recording(
            id=None,
            started_at=(started_at.isoformat() if started_at else datetime.now(UTC).isoformat()),
            ended_at=None,
            source=RecordingSource.MANUAL,
            detected_title=title or "Untitled",
            display_title=title or "Untitled",
            audio_path=None, audio_deleted_at=None, duration_ms=1000,
            status=RecordingStatus.DONE, error_message=None,
        ))
        assert rec.id is not None
        return rec.id

    _import.calls = calls
    return _import


def _seed_recording_with_todo(db, done_at: str | None) -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-07-14T09:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    assert rec.id is not None
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="t", one_line="x", summary="x",
        key_decisions=[], my_todos=[TodoItem(task="Do A")],
        action_items_others=[], follow_ups=[], topics=[],
        generated_at=datetime.now(UTC).isoformat(), model_used="claude-sonnet-4-6",
    ))
    TodoStateRepo(db).mark_done(rec.id, 0, done_at is not None, task_text="Do A")
    if done_at is not None:
        # mark_done stamps "now" as done_at; force the exact seeded value.
        with db.connect() as conn:
            conn.execute(
                "UPDATE todo_state SET done_at = ? WHERE recording_id = ? AND todo_index = 0",
                (done_at, rec.id),
            )
            conn.commit()
    return rec.id


def test_happy_path_imports_and_acks(tmp_path):
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        seed_outbox(t, "uid-1", title="Standup")
        importer = fake_import(db)

        report = run_sync(db, t, import_recording=importer, now_iso="2026-07-14T12:00:00+00:00")

        assert len(report.imported) == 1
        uid, rid = report.imported[0]
        assert uid == "uid-1"
        assert t.list_files("outbox") == []
        assert PhoneImportRepo(db).recording_id_for("uid-1") == rid

        ack = json.loads(t.read_text("sync/desktop_ack.json"))
        assert ack["imported"][0] == {"uid": "uid-1", "recording_id": rid, "result": "imported"}
        assert t.read_text("library/manifest.json") is not None
        assert t.read_text("library/meetings.json") is not None
    finally:
        db.close()


def test_bad_duration_ms_fails_only_that_file(tmp_path):
    """A malformed duration_ms in one sidecar must not abort the whole
    cycle: the bad file is reported as a failure and left in place, the
    other outbox item still imports, and library + ack still get written."""
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        seed_outbox(t, "uid-good", title="Good")
        t.push_text("fake-audio-bytes", "outbox/rec_uid-bad.m4a")
        t.push_text(
            json.dumps({
                "uid": "uid-bad", "title": "Bad", "source": "memo",
                "started_at": "2026-07-14T10:00:00+00:00",
                "duration_ms": "abc",
            }),
            "outbox/rec_uid-bad.json",
        )
        importer = fake_import(db)

        report = run_sync(db, t, import_recording=importer, now_iso="2026-07-14T12:00:00+00:00")

        assert len(report.imported) == 1
        assert report.imported[0][0] == "uid-good"
        assert len(report.failures) == 1
        assert report.failures[0][0] == "outbox/rec_uid-bad.m4a"
        names = {f.name for f in t.list_files("outbox")}
        assert "outbox/rec_uid-bad.m4a" in names and "outbox/rec_uid-bad.json" in names
        assert "outbox/rec_uid-good.m4a" not in names
        assert PhoneImportRepo(db).recording_id_for("uid-good") is not None
        assert PhoneImportRepo(db).recording_id_for("uid-bad") is None
        ack = json.loads(t.read_text("sync/desktop_ack.json"))
        uids_acked = {e["uid"] for e in ack["imported"]}
        assert "uid-good" in uids_acked
        assert t.read_text("library/manifest.json") is not None
        assert t.read_text("library/meetings.json") is not None
    finally:
        db.close()


def test_second_run_is_idempotent(tmp_path):
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        importer = fake_import(db)
        seed_outbox(t, "uid-1")
        run_sync(db, t, import_recording=importer, now_iso="2026-07-14T12:00:00+00:00")

        seed_outbox(t, "uid-1")  # stale duplicate copy re-appears
        report = run_sync(db, t, import_recording=importer, now_iso="2026-07-14T12:05:00+00:00")

        assert report.skipped_known == 1
        assert len(importer.calls) == 1
        assert t.list_files("outbox") == []
    finally:
        db.close()


def test_missing_sidecar_leaves_files_and_reports_failure(tmp_path):
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        t.push_text("audio-bytes", "outbox/rec_uid-1.m4a")
        importer = fake_import(db)

        report = run_sync(db, t, import_recording=importer, now_iso="2026-07-14T12:00:00+00:00")

        assert len(report.failures) == 1
        assert report.failures[0][0] == "outbox/rec_uid-1.m4a"
        assert [f.name for f in t.list_files("outbox")] == ["outbox/rec_uid-1.m4a"]
        assert importer.calls == []
        ack = json.loads(t.read_text("sync/desktop_ack.json"))
        assert ack["imported"][0]["uid"] == "uid-1"
    finally:
        db.close()


def test_pull_size_mismatch_leaves_files(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        seed_outbox(t, "uid-1")
        importer = fake_import(db)

        def _truncated_pull(name: str, dest: Path) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")

        monkeypatch.setattr(t, "pull", _truncated_pull)

        report = run_sync(db, t, import_recording=importer, now_iso="2026-07-14T12:00:00+00:00")

        assert len(report.failures) == 1
        assert PhoneImportRepo(db).recording_id_for("uid-1") is None
        names = {f.name for f in t.list_files("outbox")}
        assert "outbox/rec_uid-1.m4a" in names and "outbox/rec_uid-1.json" in names
    finally:
        db.close()


def test_bad_sidecar_leaves_files_and_reports_failure(tmp_path):
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        seed_outbox(t, "uid-1", source="carrier_pigeon")  # ContractError source
        importer = fake_import(db)

        report = run_sync(db, t, import_recording=importer, now_iso="2026-07-14T12:00:00+00:00")

        assert len(report.failures) == 1
        assert report.failures[0][0] == "outbox/rec_uid-1.m4a"
        names = {f.name for f in t.list_files("outbox")}
        assert "outbox/rec_uid-1.m4a" in names and "outbox/rec_uid-1.json" in names
        assert importer.calls == []
    finally:
        db.close()


def test_import_failure_leaves_files_and_ledger_empty(tmp_path):
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        seed_outbox(t, "uid-1")

        def _boom(src_path: str, *, title, started_at) -> int:
            raise RuntimeError("decoder exploded")

        report = run_sync(db, t, import_recording=_boom, now_iso="2026-07-14T12:00:00+00:00")

        assert report.failures == [("outbox/rec_uid-1.m4a", "decoder exploded")]
        assert PhoneImportRepo(db).recording_id_for("uid-1") is None
        names = {f.name for f in t.list_files("outbox")}
        assert "outbox/rec_uid-1.m4a" in names and "outbox/rec_uid-1.json" in names
    finally:
        db.close()


def test_toggle_lww_phone_newer_wins(tmp_path):
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        rid = _seed_recording_with_todo(db, done_at="2026-07-14T10:00:00+00:00")
        t.push_text(json.dumps([
            {"recording_id": rid, "todo_index": 0, "done": False,
             "toggled_at": "2026-07-14T11:00:00+00:00"},
        ]), "outbox/changes.json")
        seen: list[int] = []

        report = run_sync(
            db, t, import_recording=fake_import(db),
            on_todos_changed=seen.append, now_iso="2026-07-14T12:00:00+00:00",
        )

        assert report.toggles_applied == 1
        assert report.toggles_skipped_stale == 0
        states = {s.todo_index: s for s in TodoStateRepo(db).list_for_recording(rid)}
        assert states[0].done is False
        assert seen == [rid]
        # Single-writer regression: the desktop never writes changes.json,
        # so the phone's file must still be present after a sync cycle.
        assert any(f.name == "outbox/changes.json" for f in t.list_files("outbox"))
    finally:
        db.close()


def test_toggle_lww_desktop_newer_wins(tmp_path):
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        rid = _seed_recording_with_todo(db, done_at="2026-07-14T12:00:00+00:00")
        t.push_text(json.dumps([
            {"recording_id": rid, "todo_index": 0, "done": False,
             "toggled_at": "2026-07-14T11:00:00+00:00"},
        ]), "outbox/changes.json")
        seen: list[int] = []

        report = run_sync(
            db, t, import_recording=fake_import(db),
            on_todos_changed=seen.append, now_iso="2026-07-14T12:30:00+00:00",
        )

        assert report.toggles_applied == 0
        assert report.toggles_skipped_stale == 1
        states = {s.todo_index: s for s in TodoStateRepo(db).list_for_recording(rid)}
        assert states[0].done is True
        assert seen == []
    finally:
        db.close()


def test_toggle_lww_mixed_z_and_offset_suffix_compares_correctly(tmp_path):
    """done_at uses a Z suffix, toggled_at uses +00:00 with a fractional
    second in the SAME second (10:00:00). Chronologically toggled_at
    (10:00:00.5) is later than done_at (10:00:00) so the toggle must apply.
    A naive string comparison gets this backwards -- 'Z' (0x5A) sorts
    greater than '.' (0x2E), so 'done_at Z' > 'toggled_at .5+00:00' as
    strings, wrongly marking the toggle stale. Only the datetime.fromisoformat
    comparison path gets this right."""
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        rid = _seed_recording_with_todo(db, done_at="2026-07-14T10:00:00Z")
        t.push_text(json.dumps([
            {"recording_id": rid, "todo_index": 0, "done": False,
             "toggled_at": "2026-07-14T10:00:00.500000+00:00"},
        ]), "outbox/changes.json")

        report = run_sync(
            db, t, import_recording=fake_import(db),
            now_iso="2026-07-14T12:00:00+00:00",
        )

        assert report.toggles_applied == 1
        assert report.toggles_skipped_stale == 0
        states = {s.todo_index: s for s in TodoStateRepo(db).list_for_recording(rid)}
        assert states[0].done is False
    finally:
        db.close()


def test_same_todo_twice_in_one_batch_applies_both_in_lww_order(tmp_path):
    """Entry A (10:00, done) then entry B (11:00, undone) for the SAME todo in
    one changes.json: A must persist ITS toggled_at as done_at (not wall-clock
    now, which is far ahead of phone timestamps) so B is not wrongly staled."""
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        rid = _seed_recording_with_todo(db, done_at=None)
        t.push_text(json.dumps([
            {"recording_id": rid, "todo_index": 0, "done": True,
             "toggled_at": "2026-07-14T10:00:00+00:00"},
            {"recording_id": rid, "todo_index": 0, "done": False,
             "toggled_at": "2026-07-14T11:00:00+00:00"},
        ]), "outbox/changes.json")

        report = run_sync(db, t, import_recording=fake_import(db),
                          now_iso="2026-07-14T12:00:00+00:00")

        assert report.toggles_applied == 2
        assert report.toggles_skipped_stale == 0
        states = {s.todo_index: s for s in TodoStateRepo(db).list_for_recording(rid)}
        assert states[0].done is False  # B applied over A
    finally:
        db.close()


def test_ack_changes_applied_through_is_max_seen(tmp_path):
    db = _make_db(tmp_path)
    try:
        t = phone(tmp_path)
        rid = _seed_recording_with_todo(db, done_at="2026-07-14T09:00:00+00:00")
        t.push_text(json.dumps([
            {"recording_id": rid, "todo_index": 0, "done": True,
             "toggled_at": "2026-07-14T10:00:00+00:00"},
            {"recording_id": rid, "todo_index": 0, "done": False,
             "toggled_at": "2026-07-14T15:00:00+00:00"},
        ]), "outbox/changes.json")

        run_sync(db, t, import_recording=fake_import(db), now_iso="2026-07-14T16:00:00+00:00")

        ack = json.loads(t.read_text("sync/desktop_ack.json"))
        assert ack["changes_applied_through"] == "2026-07-14T15:00:00+00:00"
    finally:
        db.close()
