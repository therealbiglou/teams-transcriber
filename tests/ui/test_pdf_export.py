"""Tests for pdf_export: HTML→PDF rendering and suffix-based dispatch."""

from __future__ import annotations

from pathlib import Path

from teams_transcriber.storage.models import (
    ActionItemOther,
    Recording,
    RecordingSource,
    RecordingStatus,
    Summary,
    TodoItem,
)


def _rec() -> Recording:
    return Recording(
        id=1, started_at="2026-05-20T15:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="Sync",
        display_title="Potter Sync", audio_path=None, audio_deleted_at=None,
        duration_ms=1_500_000, status=RecordingStatus.DONE, error_message=None,
    )


def _summary() -> Summary:
    return Summary(
        recording_id=1, title="Potter Sync", one_line="ol",
        summary="We discussed the booth.",
        key_decisions=["Ship Friday"],
        my_todos=[
            TodoItem(task="Email Jennifer", due="2026-05-22"),
            TodoItem(task="Order banner", due=None),
        ],
        action_items_others=[ActionItemOther(who="Jennifer", task="Send floor plan", due=None)],
        follow_ups=["Confirm headcount"],
        topics=["booth", "logistics"],
        generated_at="2026-05-20T16:00:00+00:00",
        model_used="claude-sonnet-4-6",
    )


def test_render_html_to_pdf_creates_valid_pdf(qapp, tmp_path: Path) -> None:
    """render_html_to_pdf should produce a file whose first 5 bytes are %PDF-."""
    from teams_transcriber.ui.pdf_export import render_html_to_pdf

    out = tmp_path / "out.pdf"
    render_html_to_pdf("<html><body><h1>Hi</h1></body></html>", str(out))

    assert out.exists(), "PDF file was not created"
    assert out.stat().st_size > 0, "PDF file is empty"
    assert out.read_bytes()[:5] == b"%PDF-", "File does not start with %PDF-"


def test_write_summary_export_md(qapp, tmp_path: Path) -> None:
    """write_summary_export to .md should produce markdown starting with '# '."""
    from teams_transcriber.ui.pdf_export import write_summary_export

    out = tmp_path / "a.md"
    write_summary_export(str(out), _summary(), _rec(), {0: True, 1: False})

    content = out.read_text(encoding="utf-8")
    assert content.startswith("# "), f"Markdown should start with '# ', got: {content[:40]!r}"


def test_write_summary_export_txt_contains_done(qapp, tmp_path: Path) -> None:
    """write_summary_export to .txt with states {0:True} should contain [x]."""
    from teams_transcriber.ui.pdf_export import write_summary_export

    out = tmp_path / "a.txt"
    write_summary_export(str(out), _summary(), _rec(), {0: True})

    content = out.read_text(encoding="utf-8")
    assert "[x]" in content, f"Expected '[x]' in plaintext output:\n{content}"


def test_write_summary_export_pdf_starts_pdf_magic(qapp, tmp_path: Path) -> None:
    """write_summary_export to .pdf should produce a file starting with %PDF-."""
    from teams_transcriber.ui.pdf_export import write_summary_export

    out = tmp_path / "a.pdf"
    write_summary_export(str(out), _summary(), _rec(), {0: True, 1: False})

    assert out.exists(), "PDF file was not created"
    assert out.read_bytes()[:5] == b"%PDF-", "File does not start with %PDF-"
