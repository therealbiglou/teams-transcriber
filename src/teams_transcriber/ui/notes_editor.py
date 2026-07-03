"""Rich-text notes editor widget with debounced auto-save.

Extracted from the original `NotesWindow` so the same editor can be embedded
in `WorkspaceWindow` for live recordings and in any "edit notes" surface.
Auto-saves to `recordings.manual_notes` after a debounce.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QTextCharFormat, QTextListFormat
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.storage import Database, RecordingRepo


class _NotesTextEdit(QTextEdit):
    """QTextEdit where Tab / Shift+Tab indent / outdent the current list item.

    Inside a bullet (or numbered) list, Tab turns the item into a sub-bullet
    (one level deeper) and Shift+Tab brings it back out. Outside a list, Tab
    behaves normally. Bullet glyphs cycle disc → circle → square by depth so
    sub-levels read distinctly.
    """

    _BULLET_STYLES = (
        QTextListFormat.Style.ListDisc,
        QTextListFormat.Style.ListCircle,
        QTextListFormat.Style.ListSquare,
    )

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            delta = -1 if e.key() == Qt.Key.Key_Backtab else 1
            if self.change_list_indent(delta):
                e.accept()
                return
        super().keyPressEvent(e)

    def change_list_indent(self, delta: int) -> bool:
        """Indent (+1) / outdent (-1) the current list item.

        Returns True if the cursor was inside a list (and the keystroke was
        therefore consumed), False otherwise.
        """
        cursor = self.textCursor()
        current = cursor.currentList()
        if current is None:
            return False
        fmt = current.format()
        new_indent = max(1, fmt.indent() + delta)
        if new_indent != fmt.indent():
            new_fmt = QTextListFormat(fmt)
            new_fmt.setIndent(new_indent)
            if fmt.style() in self._BULLET_STYLES:
                new_fmt.setStyle(self._BULLET_STYLES[(new_indent - 1) % len(self._BULLET_STYLES)])
            cursor.createList(new_fmt)
        return True  # consume Tab in a list even at min indent (no tab char)


class NotesEditor(QWidget):
    """Self-contained rich-text editor for one recording's manual notes."""

    saved = Signal(int)  # recording_id

    def __init__(
        self,
        db: Database,
        recording_id: int,
        *,
        autosave_debounce_ms: int = 1000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._recording_id = recording_id

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)

        self.editor = _NotesTextEdit()
        self.editor.setAcceptRichText(True)
        self.editor.setPlaceholderText("Start typing notes…")

        rec = RecordingRepo(db).get(recording_id)
        if rec is not None and rec.manual_notes:
            self.editor.setHtml(rec.manual_notes)

        def _toolbar_btn(text: str, tooltip: str, handler: Callable[[], None],
                         shortcut: QKeySequence.StandardKey | None = None,
                         style: str = "") -> QPushButton:
            btn = QPushButton(text)
            btn.setProperty("role", "secondary")
            btn.setToolTip(tooltip)
            if style:
                btn.setStyleSheet(btn.styleSheet() + style)
            if shortcut is not None:
                btn.setShortcut(QKeySequence(shortcut))
            btn.clicked.connect(handler)
            return btn

        toolbar.addWidget(_toolbar_btn(
            "B", "Bold (Ctrl+B)",
            self._toggle_bold, QKeySequence.StandardKey.Bold,
            style=" font-weight: 700;",
        ))
        toolbar.addWidget(_toolbar_btn(
            "I", "Italic (Ctrl+I)",
            self._toggle_italic, QKeySequence.StandardKey.Italic,
            style=" font-style: italic;",
        ))
        toolbar.addWidget(_toolbar_btn(
            "U", "Underline (Ctrl+U)",
            self._toggle_underline, QKeySequence.StandardKey.Underline,
            style=" text-decoration: underline;",
        ))
        sep = QLabel(" ")
        sep.setFixedWidth(8)
        toolbar.addWidget(sep)
        toolbar.addWidget(_toolbar_btn("• List", "Bullet list", self._bullet_list))
        toolbar.addWidget(_toolbar_btn("1. List", "Numbered list", self._numbered_list))
        toolbar.addWidget(_toolbar_btn("Clear", "Clear formatting", self._clear_formatting))
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        layout.addWidget(self.editor, 1)

        # Debounced auto-save.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(autosave_debounce_ms)
        self._debounce.timeout.connect(self.flush_now)
        self.editor.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self) -> None:
        self._debounce.start()

    def flush_now(self) -> None:
        """Persist the editor contents immediately (used on close / blur)."""
        html = self.editor.toHtml() if self.editor.toPlainText().strip() else None
        RecordingRepo(self._db).set_manual_notes(self._recording_id, html)
        self.saved.emit(self._recording_id)

    # --- formatting handlers (copied verbatim from NotesWindow) ------------

    def _toggle_bold(self) -> None:
        fmt = QTextCharFormat()
        cursor = self.editor.textCursor()
        current = cursor.charFormat().fontWeight()
        new_weight = 400 if current >= 700 else 700
        fmt.setFontWeight(new_weight)
        self._merge_format(fmt)

    def _toggle_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.editor.fontItalic())
        self._merge_format(fmt)

    def _toggle_underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.editor.fontUnderline())
        self._merge_format(fmt)

    def _merge_format(self, fmt: QTextCharFormat) -> None:
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)

    def _bullet_list(self) -> None:
        self._apply_list_style(QTextListFormat.Style.ListDisc)

    def _numbered_list(self) -> None:
        self._apply_list_style(QTextListFormat.Style.ListDecimal)

    def _apply_list_style(self, style: QTextListFormat.Style) -> None:
        cursor = self.editor.textCursor()
        list_fmt = QTextListFormat()
        list_fmt.setStyle(style)
        cursor.createList(list_fmt)

    def _clear_formatting(self) -> None:
        cursor = self.editor.textCursor()
        cursor.setCharFormat(QTextCharFormat())
