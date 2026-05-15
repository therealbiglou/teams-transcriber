import pytest

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.models import (
    Channel,
    Recording,
    RecordingSource,
    RecordingStatus,
    TranscriptSegment,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.transcripts import SearchHit, TranscriptRepo


@pytest.fixture
def recording_id(db: Database) -> int:
    rec = RecordingRepo(db).create(
        Recording(
            id=None,
            started_at="2026-05-14T10:00:00+00:00",
            ended_at=None,
            source=RecordingSource.TEAMS,
            detected_title="X",
            display_title="X",
            audio_path=None,
            audio_deleted_at=None,
            duration_ms=None,
            status=RecordingStatus.TRANSCRIBING,
            error_message=None,
        )
    )
    assert rec.id is not None
    return rec.id


def test_append_and_list_segments(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append(
        TranscriptSegment(
            id=None,
            recording_id=recording_id,
            start_ms=0,
            end_ms=2000,
            channel=Channel.ME,
            text="Hello there",
        )
    )
    repo.append(
        TranscriptSegment(
            id=None,
            recording_id=recording_id,
            start_ms=2000,
            end_ms=4500,
            channel=Channel.OTHERS,
            text="Hi back",
        )
    )
    segs = repo.list_for_recording(recording_id)
    assert [s.text for s in segs] == ["Hello there", "Hi back"]
    assert [s.channel for s in segs] == [Channel.ME, Channel.OTHERS]


def test_append_many_preserves_order(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append_many(
        [
            TranscriptSegment(None, recording_id, 0, 1000, Channel.ME, "one"),
            TranscriptSegment(None, recording_id, 1000, 2000, Channel.ME, "two"),
            TranscriptSegment(None, recording_id, 2000, 3000, Channel.ME, "three"),
        ]
    )
    segs = repo.list_for_recording(recording_id)
    assert [s.text for s in segs] == ["one", "two", "three"]


def test_fts_search_returns_hits(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append_many(
        [
            TranscriptSegment(None, recording_id, 0, 1000, Channel.OTHERS,
                              "Let's discuss the billing rewrite next quarter"),
            TranscriptSegment(None, recording_id, 1000, 2000, Channel.ME,
                              "Sounds good. I'll write the API stub."),
            TranscriptSegment(None, recording_id, 2000, 3000, Channel.OTHERS,
                              "Great, and I'll handle the migration doc."),
        ]
    )
    hits = repo.search("billing")
    assert len(hits) == 1
    assert isinstance(hits[0], SearchHit)
    assert hits[0].recording_id == recording_id
    assert "billing" in hits[0].snippet.lower()


def test_fts_search_multiword(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append(
        TranscriptSegment(None, recording_id, 0, 1000, Channel.OTHERS,
                          "Schedule the billing rewrite for July")
    )
    hits = repo.search("billing rewrite")
    assert len(hits) == 1


def test_fts_search_returns_empty_when_no_match(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append(
        TranscriptSegment(None, recording_id, 0, 1000, Channel.OTHERS, "Hello world")
    )
    assert repo.search("nonexistent") == []


def test_fts_updates_when_segment_deleted(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    seg = TranscriptSegment(None, recording_id, 0, 1000, Channel.OTHERS, "billing")
    repo.append(seg)
    assert seg.id is not None

    # Delete via SQL (no repo method needed; cascading delete via Recording delete is tested elsewhere).
    with db.connect() as conn:
        conn.execute("DELETE FROM transcript_segments WHERE id = ?", (seg.id,))
        conn.commit()
    assert repo.search("billing") == []


def test_search_handles_special_characters_safely(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append(
        TranscriptSegment(None, recording_id, 0, 1000, Channel.OTHERS, "Hello world")
    )
    # An FTS-significant character used naively would crash; the repo must escape it.
    hits = repo.search('"hello"')
    # The result is allowed to be 0 or 1 — the contract is "doesn't raise".
    assert isinstance(hits, list)


def test_search_includes_recording_title(db: Database, recording_id: int) -> None:
    repo = TranscriptRepo(db)
    repo.append(
        TranscriptSegment(None, recording_id, 0, 1000, Channel.ME, "Quarterly planning chat")
    )
    hits = repo.search("planning")
    assert len(hits) == 1
    assert hits[0].recording_title == "X"  # display_title from fixture
