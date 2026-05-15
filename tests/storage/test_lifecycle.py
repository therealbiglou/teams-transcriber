"""End-to-end: exercise every repo together against one Database to confirm integration."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from teams_transcriber.storage import (
    ActionItemOther,
    AudioRetentionPruner,
    Channel,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    Summary,
    SummaryRepo,
    TodoItem,
    TodoStateRepo,
    TranscriptRepo,
    TranscriptSegment,
    build_database,
)


def test_full_lifecycle(tmp_path: Path) -> None:
    db = build_database(tmp_path / "tt.db")
    db.initialize()

    recordings = RecordingRepo(db)
    transcripts = TranscriptRepo(db)
    summaries = SummaryRepo(db)
    todos = TodoStateRepo(db)

    started = datetime(2026, 5, 14, 10, 0, tzinfo=UTC)
    audio = tmp_path / "rec.opus"
    audio.write_bytes(b"audio")

    # 1. Create a recording in 'recording' state.
    rec = recordings.create(
        Recording(
            id=None,
            started_at=started.isoformat(),
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="Q2 roadmap sync | Microsoft Teams",
            display_title=None,
            audio_path=str(audio),
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.RECORDING,
            error_message=None,
        )
    )
    assert rec.id is not None

    # 2. Append live transcript segments.
    transcripts.append_many(
        [
            TranscriptSegment(None, rec.id, 0, 2000, Channel.OTHERS,
                              "Welcome everyone, let's talk about the billing rewrite."),
            TranscriptSegment(None, rec.id, 2000, 4500, Channel.ME,
                              "Sure, I'll own the API stub by Friday."),
            TranscriptSegment(None, rec.id, 4500, 7000, Channel.OTHERS,
                              "Sarah will handle the migration doc."),
        ]
    )

    # 3. Move through state transitions.
    recordings.update_status(rec.id, RecordingStatus.TRANSCRIBING)
    recordings.finalize(rec.id, ended_at=(started + timedelta(minutes=47)).isoformat(),
                         duration_ms=47 * 60 * 1000)
    recordings.update_status(rec.id, RecordingStatus.SUMMARIZING)

    # 4. Persist a summary (note `title` was added in Task 7 fixes).
    summaries.upsert(
        Summary(
            recording_id=rec.id,
            title="Q2 roadmap sync",
            one_line="Aligned on billing rewrite; I own API stub by Friday.",
            summary="Discussed the billing rewrite. Agreed to ship in July.",
            key_decisions=["Billing rewrite scheduled for July release"],
            my_todos=[TodoItem(task="Write API stub spec", due="2026-05-16")],
            action_items_others=[ActionItemOther(who="Sarah", task="Review migration doc")],
            follow_ups=["Revisit pricing tiers after legal review"],
            topics=["billing", "roadmap"],
            generated_at=datetime.now(UTC).isoformat(),
            model_used="claude-sonnet-4-6",
        )
    )
    recordings.update_status(rec.id, RecordingStatus.DONE)
    recordings.set_display_title(rec.id, "Q2 roadmap sync")

    # 5. Mark the my_todo as done.
    todos.mark_done(rec.id, todo_index=0, done=True, task_text="Write API stub spec")

    # 6. Search across the transcript.
    hits = transcripts.search("billing rewrite")
    assert len(hits) >= 1
    assert any(h.recording_id == rec.id for h in hits)

    # 7. Read back everything end-to-end.
    fetched = recordings.get(rec.id)
    assert fetched is not None
    assert fetched.display_title == "Q2 roadmap sync"
    assert fetched.status == RecordingStatus.DONE

    summary = summaries.get(rec.id)
    assert summary is not None
    assert summary.title == "Q2 roadmap sync"
    assert summary.my_todos[0].task == "Write API stub spec"

    state = todos.list_for_recording(rec.id)
    assert state[0].done is True

    segs = transcripts.list_for_recording(rec.id)
    assert len(segs) == 3

    # 8. Run retention 100 days later and confirm audio is pruned, transcripts/summaries kept.
    future = started + timedelta(days=100)
    pruner = AudioRetentionPruner(db, retention_days=30, now=lambda: future)
    report = pruner.run()
    assert report.deleted_count == 1
    assert not audio.exists()

    refetched = recordings.get(rec.id)
    assert refetched is not None
    assert refetched.audio_path is None
    assert refetched.audio_deleted_at is not None
    # Transcripts and summaries must still be present.
    assert len(transcripts.list_for_recording(rec.id)) == 3
    assert summaries.get(rec.id) is not None

    db.close()


def test_database_can_be_reopened(tmp_path: Path) -> None:
    """Open, write, close, reopen — data persists; migrations don't re-run destructively."""
    db_path = tmp_path / "persist.db"

    db = build_database(db_path)
    db.initialize()
    repo = RecordingRepo(db)
    rec = repo.create(
        Recording(
            id=None,
            started_at="2026-05-14T10:00:00+00:00",
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title=None,
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.DONE,
            error_message=None,
        )
    )
    rec_id = rec.id
    db.close()

    db2 = build_database(db_path)
    db2.initialize()
    again = RecordingRepo(db2).get(rec_id)  # type: ignore[arg-type]
    assert again is not None
    assert again.detected_title == "X"
    db2.close()
