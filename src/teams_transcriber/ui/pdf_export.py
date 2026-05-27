"""HTML→PDF rendering + export-by-extension dispatch (Qt side of export).

summary_export does the Qt-free serialization; this module adds PDF (via
QtPrintSupport, bundled with PySide6) and picks the format from the path suffix.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrinter

from teams_transcriber import summary_export
from teams_transcriber.storage.models import Recording, Summary


def render_html_to_pdf(html: str, out_path: str) -> None:
    doc = QTextDocument()
    doc.setHtml(html)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(out_path)
    doc.print_(printer)


def write_summary_export(
    path: str, summary: Summary, recording: Recording, todo_states: dict[int, bool],
) -> None:
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        render_html_to_pdf(summary_export.to_html(summary, recording, todo_states), path)
    elif suffix == ".txt":
        Path(path).write_text(
            summary_export.to_plaintext(summary, recording, todo_states), encoding="utf-8",
        )
    else:  # .md or unknown
        Path(path).write_text(
            summary_export.to_markdown(summary, recording, todo_states), encoding="utf-8",
        )
