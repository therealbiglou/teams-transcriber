"""Tests for the desktop→phone library export builder.

Uses `build_database` (full migration set) rather than the plain `db` fixture,
which only applies schema v1 — phone_imports (v7), todo_state, chat_messages
etc. are needed here. Seeding mirrors tests/ui/test_summary_pane.py's
`db_with_summary` fixture and tests/storage/test_phone_imports.py's db
construction.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from teams_transcriber.storage import (
    ActionItemOther,
    ChatRepo,
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


def _seed_full_recording(db) -> int:
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
    from teams_transcriber.storage import Channel, TranscriptRepo, TranscriptSegment

    TranscriptRepo(db).append_many([
        TranscriptSegment(
            id=None, recording_id=rec.id,
            start_ms=0, end_ms=5000,
            channel=Channel.ME, text="Hello from the meeting.",
        ),
    ])
    TodoStateRepo(db).upsert(rec.id, 0, "Do A", done=False)
    TodoStateRepo(db).upsert(rec.id, 1, "Do B", done=False)
    return rec.id


def test_build_library_full_mirror(tmp_path):
    db = _make_db(tmp_path)
    try:
        rid = _seed_full_recording(db)

        from teams_transcriber.phone_sync.library_export import build_library
        from teams_transcriber.storage import PhoneImportRepo

        PhoneImportRepo(db).record("uid-9", rid, "in_person")
        files = build_library(db, now_iso="2026-07-14T12:00:00+00:00")

        manifest = json.loads(files["library/manifest.json"])
        assert manifest["schema_version"] == 1

        meetings = json.loads(files["library/meetings.json"])
        entry = next(m for m in meetings if m["id"] == rid)
        assert entry["title"] and entry["started_at"] and entry["status"] == "done"
        assert entry["source"] == "in_person"          # ledger overrides "teams"
        assert entry["todo_count"] == 2 and entry["todos_done"] == 0

        detail = json.loads(files[f"library/meetings/{rid}.json"])
        assert detail["summary"]
        assert detail["my_todos"][0]["task"] and detail["my_todos"][0]["done"] is False
        assert detail["transcript"][0]["text"]
        assert detail["chat"] == []
    finally:
        db.close()


def test_build_library_includes_seeded_chat_messages(tmp_path):
    db = _make_db(tmp_path)
    try:
        rid = _seed_full_recording(db)
        ChatRepo(db).append(rid, "user", "What's the status?")
        ChatRepo(db).append(rid, "assistant", "On track.")

        from teams_transcriber.phone_sync.library_export import build_library

        files = build_library(db, now_iso="2026-07-14T12:00:00+00:00")
        detail = json.loads(files[f"library/meetings/{rid}.json"])
        assert [m["role"] for m in detail["chat"]] == ["user", "assistant"]
        assert detail["chat"][0]["content"] == "What's the status?"
    finally:
        db.close()


def test_build_library_todos_done_ignores_stale_state_rows(tmp_path):
    """Re-summarization can shrink my_todos (e.g. 5 -> 2) while todo_state
    keeps rows for the old higher indices (TodoStateRepo.seed never prunes).
    todos_done must only count done rows whose index is within the CURRENT
    summary's my_todos."""
    db = _make_db(tmp_path)
    try:
        rid = _seed_full_recording(db)  # summary has 2 my_todos
        repo = TodoStateRepo(db)
        repo.upsert(rid, 0, "Do A", done=True)            # current index, done
        repo.upsert(rid, 5, "Old stale todo", done=True)  # stale index, done

        from teams_transcriber.phone_sync.library_export import build_library

        files = build_library(db, now_iso="2026-07-14T12:00:00+00:00")
        meetings = json.loads(files["library/meetings.json"])
        entry = next(m for m in meetings if m["id"] == rid)
        assert entry["todo_count"] == 2
        assert entry["todos_done"] == 1  # stale index-5 done row must not count
    finally:
        db.close()


def test_build_library_skips_recordings_without_summary(tmp_path):
    db = _make_db(tmp_path)
    try:
        rec = RecordingRepo(db).create(Recording(
            id=None, started_at="2026-07-01T09:00:00+00:00", ended_at=None,
            source=RecordingSource.MANUAL, detected_title="t", display_title="No summary yet",
            audio_path=None, audio_deleted_at=None, duration_ms=1000,
            status=RecordingStatus.TRANSCRIBING, error_message=None,
        ))
        assert rec.id is not None

        from teams_transcriber.phone_sync.library_export import build_library

        files = build_library(db, now_iso="2026-07-14T12:00:00+00:00")

        meetings = json.loads(files["library/meetings.json"])
        entry = next(m for m in meetings if m["id"] == rec.id)
        assert entry["status"] == "transcribing"
        assert entry["one_line"] is None
        assert entry["todo_count"] == 0 and entry["todos_done"] == 0

        assert f"library/meetings/{rec.id}.json" not in files
    finally:
        db.close()
