from __future__ import annotations

from teams_transcriber.integrations.wrike_project_body import (
    build_description, build_transcript_md,
)
from teams_transcriber.storage.models import Channel, Summary, TranscriptSegment


def _summary(**kw):
    base = dict(
        recording_id=1, title="T", one_line="one", summary="The summary body.",
        key_decisions=[], my_todos=[], action_items_others=[], follow_ups=[],
        topics=[], generated_at="2026-07-24T00:00:00+00:00", model_used="m",
    )
    base.update(kw)
    return Summary(**base)


def test_description_without_decisions_is_just_summary():
    assert build_description(_summary()) == "The summary body."


def test_description_appends_key_decisions_section():
    out = build_description(_summary(key_decisions=["Ship Friday", "Use SQLite"]))
    assert out.startswith("The summary body.")
    assert "## Key decisions" in out
    assert "- Ship Friday" in out and "- Use SQLite" in out


def test_transcript_md_formats_channel_and_timestamp():
    segs = [
        TranscriptSegment(id=None, recording_id=1, start_ms=12000, end_ms=13000,
                          channel=Channel.ME, text="hello"),
        TranscriptSegment(id=None, recording_id=1, start_ms=75000, end_ms=76000,
                          channel=Channel.OTHERS, text="hi back"),
    ]
    md = build_transcript_md(segs)
    assert md.startswith("# Transcript")
    assert "**ME** 00:12 hello" in md
    assert "**OTHERS** 01:15 hi back" in md


def test_transcript_md_empty():
    assert "no transcript" in build_transcript_md([]).lower()
