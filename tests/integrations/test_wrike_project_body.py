from __future__ import annotations

from teams_transcriber.integrations.wrike_project_body import (
    build_description,
    build_task_description,
    build_transcript_md,
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


def test_description_without_decisions_is_plain_body():
    # No special chars / newlines → HTML output is identical to the raw body.
    assert build_description(_summary()) == "The summary body."


def test_description_appends_key_decisions_as_html():
    out = build_description(_summary(key_decisions=["Ship Friday", "Use SQLite"]))
    assert out == (
        "The summary body."
        "<br/><br/><b>Key decisions</b>"
        "<ul><li>Ship Friday</li><li>Use SQLite</li></ul>"
    )


def test_description_newlines_become_br_and_html_is_escaped():
    out = build_description(
        _summary(
            summary="Line 1\nLine 2 with <tag> & ampersand",
            key_decisions=["Use SQLite <fts5> & keep it"],
        )
    )
    assert out == (
        "Line 1<br/>Line 2 with &lt;tag&gt; &amp; ampersand"
        "<br/><br/><b>Key decisions</b>"
        "<ul><li>Use SQLite &lt;fts5&gt; &amp; keep it</li></ul>"
    )


def test_transcript_md_formats_channel_and_timestamp():
    segs = [
        TranscriptSegment(id=None, recording_id=1, start_ms=12000, end_ms=13000,
                          channel=Channel.ME, text="hello"),
        TranscriptSegment(id=None, recording_id=1, start_ms=75000, end_ms=76000,
                          channel=Channel.OTHERS, text="hi back"),
    ]
    md = build_transcript_md(segs)
    assert md == (
        "# Transcript\n\n"
        "**ME** 00:12 hello\n"
        "**OTHERS** 01:15 hi back"
    )


def test_transcript_md_empty():
    assert build_transcript_md([]) == "# Transcript\n\n_(no transcript)_"


def test_task_description_escapes_html_and_converts_newlines():
    out = build_task_description("Line 1\nLine 2 with <tag> & ampersand")
    assert out == "Line 1<br/>Line 2 with &lt;tag&gt; &amp; ampersand"


def test_task_description_plain_text_is_unchanged():
    assert build_task_description("A short context blurb.") == "A short context blurb."
