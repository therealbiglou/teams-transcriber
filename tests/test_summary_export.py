from teams_transcriber.storage.models import (
    Summary, TodoItem, ActionItemOther, Recording, RecordingSource, RecordingStatus,
)
from teams_transcriber import summary_export


def _rec():
    return Recording(
        id=1, started_at="2026-05-20T15:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="Sync",
        display_title="Potter Sync", audio_path=None, audio_deleted_at=None,
        duration_ms=1_500_000, status=RecordingStatus.DONE, error_message=None,
        manual_notes="<p>fiberglass not <b>pierglass</b></p>",
    )


def _summary():
    return Summary(
        recording_id=1, title="Potter Sync", one_line="ol",
        summary="We discussed the booth.", key_decisions=["Ship Friday"],
        my_todos=[TodoItem(task="Email Jennifer", due="2026-05-22"),
                  TodoItem(task="Order banner", due=None)],
        action_items_others=[ActionItemOther(who="Jennifer", task="Send floor plan", due=None)],
        follow_ups=["Confirm headcount"], topics=["booth", "logistics"],
        generated_at="2026-05-20T16:00:00+00:00", model_used="claude-sonnet-4-6",
    )


def test_markdown_includes_sections_and_todo_state():
    md = summary_export.to_markdown(_summary(), _rec(), {0: True, 1: False})
    assert "# Potter Sync" in md
    assert "We discussed the booth." in md
    assert "- [x] Email Jennifer (due 2026-05-22)" in md
    assert "- [ ] Order banner" in md
    assert "Jennifer: Send floor plan" in md
    assert "- Ship Friday" in md
    assert "- Confirm headcount" in md


def test_plaintext_has_no_markdown_bullets_and_shows_done():
    txt = summary_export.to_plaintext(_summary(), _rec(), {0: True, 1: False})
    assert "Potter Sync" in txt
    assert "[x] Email Jennifer" in txt
    assert not txt.split("\n")[0].startswith("#")


def test_html_is_well_formed_and_escapes_summary():
    s = _summary()
    s.summary = "a < b & c"
    html = summary_export.to_html(s, _rec(), {0: False, 1: False})
    assert "<body" in html.lower()
    assert "a &lt; b &amp; c" in html
    assert "Email Jennifer" in html


def test_strip_html_renders_notes_as_text():
    assert summary_export._strip_html("<p>hello <b>world</b></p>").strip() == "hello world"


def test_html_omits_notes_section_when_tags_only():
    rec = _rec()
    rec.manual_notes = "<br>"  # tags only, no real content
    html = summary_export.to_html(_summary(), rec, {})
    assert "My notes" not in html


def test_html_includes_notes_section_when_present():
    html = summary_export.to_html(_summary(), _rec(), {})
    assert "My notes" in html


def test_notes_inner_html_strips_wrapper_and_pt_font():
    raw = ('<!DOCTYPE HTML><html><head></head>'
           '<body style="font-family:\'Sans Serif\'; font-size:9pt;">'
           '<b>hi</b> <span style="font-size:9pt;">x</span></body></html>')
    inner = summary_export._notes_inner_html(raw)
    assert "9pt" not in inner
    assert "<body" not in inner.lower()
    assert "<b>hi</b>" in inner


def test_pdf_html_normalizes_tiny_notes_font():
    rec = _rec()
    rec.manual_notes = (
        '<!DOCTYPE HTML><html><head></head>'
        '<body style="font-family:\'Sans Serif\'; font-size:9pt;">'
        '<ul><li>a note</li></ul></body></html>'
    )
    html = summary_export.to_html(_summary(), rec, {})
    assert "9pt" not in html                  # baked-in point size removed
    assert "font-size:14px" in html           # notes wrapped at a readable size
    assert "a note" in html                   # content preserved
    assert html.lower().count("<body") == 1   # editor's nested <body> stripped
