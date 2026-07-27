"""Pure builders for the Wrike project export (no client, no db).

The project *description* is a Wrike rich-text (HTML) field. Markdown does not
render there — verified against the live API 2026-07-25: ``##``/``-`` show
literally and ``\n`` collapses to a space. So ``build_description`` emits the
HTML subset Wrike documents as supported (``<b>``, ``<br/>``, ``<ul><li>``),
with content HTML-escaped. The transcript, by contrast, is a separate ``.md``
*attachment* (a real file), so ``build_transcript_md`` stays Markdown.
"""

from __future__ import annotations

from html import escape

from teams_transcriber.storage.models import Summary, TranscriptSegment


def _html_text(text: str) -> str:
    # Escape &, <, > then turn newlines into <br/> so multi-line summaries keep
    # their breaks in Wrike's HTML description field.
    return escape(text, quote=False).replace("\n", "<br/>")


def build_description(summary: Summary) -> str:
    body = _html_text(summary.summary or "")
    if summary.key_decisions:
        items = "".join(f"<li>{_html_text(d)}</li>" for d in summary.key_decisions)
        return f"{body}<br/><br/><b>Key decisions</b><ul>{items}</ul>"
    return body


def build_task_description(statement: str, context: str | None = None) -> str:
    """HTML for a task's *description* field.

    Renders the original full item ``statement`` first (the exported task's
    title is now a short paraphrase, so the full statement has to live
    somewhere), then -- if a transcript-grounded ``context`` blurb is
    supplied -- a blank line followed by that context. Same escaping +
    ``<br/>`` rule as ``build_description``. With no context, renders just
    the statement.
    """
    body = _html_text(statement)
    if context:
        return f"{body}<br/><br/>{_html_text(context)}"
    return body


def _mmss(ms: int) -> str:
    total = max(0, ms // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


def build_transcript_md(segments: list[TranscriptSegment]) -> str:
    if not segments:
        return "# Transcript\n\n_(no transcript)_"
    lines = [
        f"**{s.channel.value.upper()}** {_mmss(s.start_ms)} {s.text}"
        for s in segments
    ]
    return "# Transcript\n\n" + "\n".join(lines)
