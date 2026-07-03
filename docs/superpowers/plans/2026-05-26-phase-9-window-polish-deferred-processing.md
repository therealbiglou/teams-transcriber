# Phase 9 — Window Polish + Deferred Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every top-level window movable/resizable with the main-app
controls; defer transcription/summarization while a notes window is open;
color the todo chip by completion; rewrite the transcript view as a single
smooth-scrolling selectable document; and make all summary text selectable.

**Architecture:** A `FramelessWindowMixin` + a configurable `TitleBar` unify
window chrome across `QMainWindow`/`QWidget`/`QDialog` hosts. The pipeline
gains a UI-agnostic `processing_gate` callable; `App` supplies a thread-safe
predicate keyed on open workspace windows and drives the waiting UI from the
same predicate. The transcript view moves from a per-row `QListWidget` to a
read-only `QTextEdit`. No DB migration (the new `WAITING_FOR_NOTES` status is a
`StrEnum` value stored as text).

**Tech Stack:** Python 3.11, PySide6 (Qt 6), pytest, SQLite. Run tests with
`uv run pytest` (the project uses `uv`; if `uv` is not on PATH it lives at
`C:\Users\brian\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe`).

Spec: `docs/superpowers/specs/2026-05-26-phase-9-window-polish-deferred-processing-design.md`.

> **Note on `uv` invocation:** all `pytest` commands below are written as
> `uv run pytest …`. If `uv` is not resolvable, prefix with the full path
> above.

> **Qt test note:** widget tests need a `QApplication`. The repo's
> `tests/ui/` already constructs one (look for a `qapp` fixture / `make_app()`
> usage in `tests/ui/test_workspace_window.py`); reuse that pattern. Run UI
> tests headless with the offscreen platform: set
> `QT_QPA_PLATFORM=offscreen` if a display isn't available.

---

## File Structure

**Create:**
- `src/teams_transcriber/ui/frameless.py` — `FramelessWindowMixin` (move +
  edge-resize + rounded corners + maximize toggle).
- `tests/ui/test_frameless.py` — mixin edge math + TitleBar config tests.
- `tests/ui/test_transcript_window.py` — chrome + load tests (may already be
  partially present; extend).
- `tests/ui/test_meeting_card.py` — todo chip color/text tests.
- `tests/ui/test_summary_pane.py` — selectable-labels + todo signal tests
  (extend if present).
- `tests/test_pipeline_defer.py` — gate / release / recovery tests.

**Modify:**
- `src/teams_transcriber/ui/title_bar.py` — configurable controls + extras +
  title text.
- `src/teams_transcriber/ui/main_window.py` — consume the mixin.
- `src/teams_transcriber/ui/workspace_window.py` — shared chrome + resize +
  `show_waiting_for_processing()`.
- `src/teams_transcriber/ui/transcript_window.py` — shared chrome + resize.
- `src/teams_transcriber/ui/settings_dialog.py`,
  `first_run_wizard.py`, `update_dialog.py` — themed frameless chrome.
- `src/teams_transcriber/ui/live_transcript_view.py` — `QTextEdit` rewrite.
- `src/teams_transcriber/ui/meeting_card.py` — todo completion chip.
- `src/teams_transcriber/ui/history_list.py` — 4-tuple rows w/ `todos_done`.
- `src/teams_transcriber/ui/summary_pane.py` — `todo_state_changed` signal +
  selectable audit.
- `src/teams_transcriber/ui/app.py` — gate predicate, waiting UI, release on
  close, 4-tuple history rows, card live-refresh.
- `src/teams_transcriber/storage/models.py` — `WAITING_FOR_NOTES` status.
- `src/teams_transcriber/pipeline.py` — gate + `release_processing` +
  recovery branch.
- `src/teams_transcriber/ui/meeting_card.py` `_status_chip` — waiting chip.

---

## FEATURE E — Selectable summary text (warm-up)

### Task E1: Audit and complete selectable labels in SummaryPane

**Files:**
- Modify: `src/teams_transcriber/ui/summary_pane.py`
- Test: `tests/ui/test_summary_pane.py`

Background: `_section_card` already calls `_make_selectable` on each `QLabel`
body widget, and title/meta are selectable. The gaps are labels created
*outside* `_section_card`: the "Summary" body label (line 145 builds
`QLabel(summary.summary)` then passes through `_section_card`, so it IS
covered), the "No summary yet" / "Recording not found" placeholders, and the
`my notes` rich-text label. The goal: **every** visible text `QLabel` in the
pane has selectable interaction flags.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_summary_pane.py
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from teams_transcriber.ui.summary_pane import SummaryPane


def _all_labels(widget):
    return widget.findChildren(QLabel)


def test_every_summary_label_is_selectable(qapp, summary_db):
    # summary_db: a Database fixture with one DONE recording + summary.
    pane = SummaryPane(summary_db.db)
    pane.show_recording(summary_db.recording_id)
    labels = _all_labels(pane)
    assert labels, "expected summary labels to be present"
    sel = Qt.TextInteractionFlag.TextSelectableByMouse
    for lbl in labels:
        # Section headers are decorative; everything else must be selectable.
        if lbl.text() in {"Summary", "My notes", "My todos", "Key decisions",
                           "Follow-ups", "Action items for others", "Topics"}:
            continue
        assert lbl.textInteractionFlags() & sel, f"not selectable: {lbl.text()!r}"
```

> If `tests/ui/test_summary_pane.py` and a `summary_db` fixture don't exist,
> create them. Build `summary_db` by constructing an in-memory/temp `Database`,
> inserting a `Recording` (status DONE) via `RecordingRepo`, and a `Summary`
> via `SummaryRepo`. Mirror the fixtures in `tests/test_storage.py` /
> `tests/test_summarizer.py` for exact repo signatures.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_summary_pane.py::test_every_summary_label_is_selectable -v`
Expected: FAIL (the rich-text "my notes" label and bare placeholder labels are not selectable).

- [ ] **Step 3: Make all labels selectable**

In `summary_pane.py`:
- Wrap the notes label with `_make_selectable`:
  ```python
  if rec.manual_notes:
      notes_label = QLabel()
      notes_label.setTextFormat(Qt.TextFormat.RichText)
      notes_label.setText(rec.manual_notes)
      _make_selectable(notes_label)
      self._layout.addWidget(_section_card("My notes", [notes_label]))
  ```
- Wrap the bare placeholders:
  ```python
  self._layout.addWidget(_make_selectable(QLabel("Recording not found.")))
  ...
  self._layout.addWidget(_make_selectable(QLabel("No summary yet for this recording.")))
  ```
- Note: the to-do `QCheckBox` rows are interactive controls, not labels, so
  they are out of scope for selectable text (expected).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ui/test_summary_pane.py::test_every_summary_label_is_selectable -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/summary_pane.py tests/ui/test_summary_pane.py
git commit -m "feat(ui): make every summary-pane label selectable"
```

---

## FEATURE C — Todo completion chip

### Task C1: MeetingCard renders a completion-aware todo chip

**Files:**
- Modify: `src/teams_transcriber/ui/meeting_card.py:27-104`
- Test: `tests/ui/test_meeting_card.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_meeting_card.py
from teams_transcriber.storage.models import Recording, RecordingSource, RecordingStatus
from teams_transcriber.ui.meeting_card import MeetingCard, _todo_chip_text, _todo_chip_variant


def test_todo_chip_text_and_variant():
    assert _todo_chip_text(3, 0) == "3 todos | 0 complete"
    assert _todo_chip_text(1, 0) == "1 todo | 0 complete"
    assert _todo_chip_text(3, 1) == "3 todos | 1 complete"
    assert _todo_chip_text(3, 3) == "3 todos | 3 complete"

    assert _todo_chip_variant(3, 0) == "error"    # none done -> red
    assert _todo_chip_variant(3, 1) == "warn"     # some done -> amber
    assert _todo_chip_variant(3, 3) == "success"  # all done  -> green
    assert _todo_chip_variant(1, 0) == "error"


def _rec():
    return Recording(
        id=7, started_at="2026-05-26T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title="t",
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    )


def test_card_shows_completion_chip(qapp):
    card = MeetingCard(_rec(), one_line=None, todo_count=3, todos_done=1)
    chip = card.findChild(type(card), None)  # placeholder; see note
    # Find the chip QLabel by its role property.
    from PySide6.QtWidgets import QLabel
    chips = [w for w in card.findChildren(QLabel) if w.property("role") == "chip"]
    assert len(chips) == 1
    assert chips[0].text() == "3 todos | 1 complete"
    assert chips[0].property("variant") == "warn"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_meeting_card.py -v`
Expected: FAIL (`_todo_chip_text`/`_todo_chip_variant` undefined; `MeetingCard`
has no `todos_done` parameter).

- [ ] **Step 3: Implement the chip helpers + parameter**

In `meeting_card.py` add module-level helpers:

```python
def _todo_chip_text(total: int, done: int) -> str:
    noun = "todo" if total == 1 else "todos"
    return f"{total} {noun} | {done} complete"


def _todo_chip_variant(total: int, done: int) -> str:
    if done >= total:
        return "success"
    if done == 0:
        return "error"
    return "warn"
```

Change the constructor signature and footer block:

```python
def __init__(
    self,
    recording: Recording,
    one_line: str | None,
    todo_count: int,
    todos_done: int = 0,
    parent: QWidget | None = None,
) -> None:
    ...
    if todo_count > 0:
        footer = QHBoxLayout()
        todo_chip = QLabel(_todo_chip_text(todo_count, todos_done))
        todo_chip.setProperty("role", "chip")
        todo_chip.setProperty("variant", _todo_chip_variant(todo_count, todos_done))
        style = todo_chip.style()
        if style is not None:
            style.unpolish(todo_chip)
            style.polish(todo_chip)
        footer.addWidget(todo_chip)
        footer.addStretch(1)
        outer.addLayout(footer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ui/test_meeting_card.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/meeting_card.py tests/ui/test_meeting_card.py
git commit -m "feat(ui): completion-aware todo chip on meeting cards"
```

### Task C2: Waiting-for-notes status chip

**Files:**
- Modify: `src/teams_transcriber/ui/meeting_card.py:128-144` (`_status_chip`)
- Test: `tests/ui/test_meeting_card.py`

(Depends on Task B1 adding the enum value; if doing C before B, add the enum
value first — see Task B1 Step 3.)

- [ ] **Step 1: Write the failing test**

```python
def test_waiting_for_notes_status_chip(qapp):
    from teams_transcriber.ui.meeting_card import _status_chip
    from teams_transcriber.storage.models import RecordingStatus
    chip = _status_chip(RecordingStatus.WAITING_FOR_NOTES)
    assert chip is not None
    assert chip.text() == "Waiting for notes"
    assert chip.property("variant") == "warn"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ui/test_meeting_card.py::test_waiting_for_notes_status_chip -v`
Expected: FAIL (KeyError/None — status not mapped).

- [ ] **Step 3: Map the status**

In `_status_chip`'s `label_variant` dict add:
```python
RecordingStatus.WAITING_FOR_NOTES:   ("Waiting for notes", "warn"),
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/ui/test_meeting_card.py::test_waiting_for_notes_status_chip -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/meeting_card.py tests/ui/test_meeting_card.py
git commit -m "feat(ui): 'Waiting for notes' status chip"
```

### Task C3: History rows carry `todos_done`; live refresh on toggle

**Files:**
- Modify: `src/teams_transcriber/ui/history_list.py:36-63,111-130`
- Modify: `src/teams_transcriber/ui/summary_pane.py` (`todo_state_changed`)
- Modify: `src/teams_transcriber/ui/app.py:206-226` + `_build_main_content`
- Test: `tests/ui/test_summary_pane.py`, `tests/ui/test_history_list.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/ui/test_summary_pane.py
def test_toggling_todo_emits_state_changed(qapp, summary_db):
    from PySide6.QtWidgets import QCheckBox
    pane = SummaryPane(summary_db.db)
    received = []
    pane.todo_state_changed.connect(received.append)
    pane.show_recording(summary_db.recording_id)  # summary_db has >=1 todo
    cb = pane.findChild(QCheckBox)
    assert cb is not None
    cb.setChecked(not cb.isChecked())
    assert received == [summary_db.recording_id]
```

```python
# tests/ui/test_history_list.py
def test_set_recordings_accepts_todos_done(qapp):
    from teams_transcriber.ui.history_list import HistoryList
    from teams_transcriber.storage.models import Recording, RecordingSource, RecordingStatus
    rec = Recording(id=1, started_at="2026-05-26T10:00:00+00:00", ended_at=None,
                    source=RecordingSource.MANUAL, detected_title="t",
                    display_title="t", audio_path=None, audio_deleted_at=None,
                    duration_ms=1000, status=RecordingStatus.DONE, error_message=None)
    hl = HistoryList()
    hl.set_recordings([(rec, "one line", 3, 1)])  # 4-tuple with todos_done
    assert 1 in hl._cards
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/ui/test_summary_pane.py::test_toggling_todo_emits_state_changed tests/ui/test_history_list.py -v`
Expected: FAIL (`todo_state_changed` undefined; `set_recordings` unpacks a
3-tuple).

- [ ] **Step 3a: Add the signal in SummaryPane**

Add to the `Signal` block:
```python
todo_state_changed = Signal(int)   # recording_id — a checkbox toggled
```
In `_build_todos_card`, after the upsert, emit:
```python
def _on_toggle(checked, idx=i, task=td.task):
    todo_repo.upsert(summary.recording_id, idx, task, checked)
    self.todo_state_changed.emit(summary.recording_id)
cb.toggled.connect(_on_toggle)
```
(Replace the existing inline lambda.)

- [ ] **Step 3b: Widen the history tuple to include `todos_done`**

In `history_list.py`:
- `set_recordings` signature → `rows: Iterable[tuple[Recording, str | None, int, int]]`.
- The grouping dict type and the unpack loop:
  ```python
  for rec, one_line, todo_count, todos_done in items:
      assert rec.id is not None
      card = MeetingCard(rec, one_line=one_line, todo_count=todo_count, todos_done=todos_done)
  ```
- `filter_for_bucket`: change the type hints from
  `list[tuple[Recording, str | None, int]]` to
  `list[tuple[Recording, str | None, int, int]]` (the body only indexes
  `r[0]`, so no logic change).

- [ ] **Step 3c: App builds 4-tuples + refreshes the card on toggle**

In `app.py` `_refresh_history`:
```python
from teams_transcriber.storage import TodoStateRepo
...
rows: list[tuple[Recording, str | None, int, int]] = []
todo_repo = TodoStateRepo(self.db)
for rec in rec_repo.list_recent(limit=200):
    if rec.id is None:
        continue
    s = sum_repo.get(rec.id)
    one_line = s.one_line if s else None
    todos = len(s.my_todos) if s else 0
    done = sum(1 for st in todo_repo.list_for_recording(rec.id) if st.done) if s else 0
    rows.append((rec, one_line, todos, done))
```
In `_build_main_content`, after wiring the other summary signals, add:
```python
self.summary.todo_state_changed.connect(lambda _rid: self._refresh_history())
```
(Refreshing the whole list is simplest and keeps the selected card; the chip
recolors immediately.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ui/test_summary_pane.py tests/ui/test_history_list.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/history_list.py src/teams_transcriber/ui/summary_pane.py src/teams_transcriber/ui/app.py tests/ui/test_summary_pane.py tests/ui/test_history_list.py
git commit -m "feat(ui): live todo-completion chip refresh from summary toggles"
```

---

## FEATURE D — Transcript view rewrite (QTextEdit)

### Task D1: Reimplement LiveTranscriptView as a read-only QTextEdit

**Files:**
- Modify: `src/teams_transcriber/ui/live_transcript_view.py` (full rewrite)
- Test: `tests/ui/test_live_transcript_view.py`

Preserve the public API (`load_segments`, `append_segment`) so
`WorkspaceWindow` and `TranscriptWindow` call sites are unchanged.

- [ ] **Step 1: Write failing tests**

```python
# tests/ui/test_live_transcript_view.py
from PySide6.QtCore import Qt
from teams_transcriber.storage.models import Channel, TranscriptSegment
from teams_transcriber.ui.live_transcript_view import LiveTranscriptView


def _seg(ms, ch, text):
    return TranscriptSegment(id=None, recording_id=1, start_ms=ms,
                             end_ms=ms + 1000, channel=ch, text=text)


def test_loads_segments_into_one_document(qapp):
    v = LiveTranscriptView()
    v.load_segments([_seg(0, Channel.ME, "hello there"),
                     _seg(2000, Channel.OTHERS, "general kenobi")])
    plain = v.toPlainText()
    assert "hello there" in plain
    assert "general kenobi" in plain
    assert "ME" in plain and "OTHERS" in plain
    assert "00:00" in plain and "00:02" in plain


def test_append_segment_adds_to_document(qapp):
    v = LiveTranscriptView()
    v.load_segments([_seg(0, Channel.ME, "first")])
    v.append_segment(_seg(1000, Channel.OTHERS, "second"))
    assert "first" in v.toPlainText()
    assert "second" in v.toPlainText()


def test_is_read_only_and_selectable(qapp):
    v = LiveTranscriptView()
    assert v.isReadOnly()
    flags = v.textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
    assert flags & Qt.TextInteractionFlag.TextSelectableByKeyboard


def test_smooth_pixel_scroll_mode(qapp):
    # QTextEdit scrolls per-pixel by default; assert we didn't force per-item.
    v = LiveTranscriptView()
    # vertical scrollbar single-step small => smooth wheel scrolling
    assert v.verticalScrollBar().singleStep() <= 20
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/ui/test_live_transcript_view.py -v`
Expected: FAIL (current class is a `QListWidget`; `toPlainText`/`isReadOnly`
not present).

- [ ] **Step 3: Rewrite the module**

```python
"""Read-only transcript document with smooth scroll + full selection.

One QTextEdit holds the whole transcript as a single selectable document, so
the user can drag-select/copy across many lines and scroll smoothly (per-pixel).
Each segment is one compact block: a colored channel tag, a mm:ss timestamp,
then the text. Live mode appends blocks via a cursor; smart auto-scroll keeps
the view pinned to the bottom only when the user is already at the bottom.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextEdit, QWidget

from teams_transcriber.storage import Channel, TranscriptSegment


def _format_ts(ms: int) -> str:
    total = max(0, ms // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


def _channel_color(channel: Channel) -> tuple[str, str]:
    """Return (label, text_color) for the inline channel tag."""
    if channel == Channel.ME:
        return "ME", "#10B981"      # emerald
    return "OTHERS", "#475569"      # slate


class LiveTranscriptView(QTextEdit):
    """Single-document transcript view (read-only, selectable, smooth scroll)."""

    AUTO_SCROLL_BOTTOM_TOLERANCE_PX = 16

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
        self.setWordWrapMode(self.wordWrapMode())  # default wrapping
        # Tight document margin; per-block spacing comes from the block HTML.
        self.document().setDocumentMargin(8)
        self.setStyleSheet(
            "QTextEdit { background: #FFFFFF; border: 1px solid #E5E7EB; "
            "border-radius: 12px; padding: 0px; }"
        )

    def _segment_html(self, segment: TranscriptSegment) -> str:
        label, color = _channel_color(segment.channel)
        ts = _format_ts(segment.start_ms)
        text = html.escape(segment.text)
        # Compact block: ~3px vertical margin via paragraph spacing.
        return (
            f'<div style="margin:0 0 3px 0;">'
            f'<span style="color:{color}; font-weight:600; font-size:11px;">{label}</span> '
            f'<span style="color:#6B7280; font-size:11px;">{ts}</span> '
            f'<span style="color:#111827;">{text}</span>'
            f'</div>'
        )

    def append_segment(self, segment: TranscriptSegment) -> None:
        was_at_bottom = self._is_scrolled_to_bottom()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(self._segment_html(segment))
        if was_at_bottom:
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())

    def load_segments(self, segments: list[TranscriptSegment]) -> None:
        """Replace contents with a fixed batch (past-recording mode)."""
        self.clear()
        if not segments:
            return
        html_blocks = "".join(self._segment_html(s) for s in segments)
        self.setHtml(html_blocks)
        # Start at the top for a finished transcript.
        self.verticalScrollBar().setValue(0)

    def _is_scrolled_to_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - self.AUTO_SCROLL_BOTTOM_TOLERANCE_PX
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ui/test_live_transcript_view.py -v`
Expected: PASS

- [ ] **Step 5: Run the workspace/transcript-window tests to confirm no API break**

Run: `uv run pytest tests/ui/test_workspace_window.py -v`
Expected: PASS (API preserved). If any test inspected `QListWidget`-specific
internals (`item`, `setItemWidget`, `count`), update it to assert against
`toPlainText()` instead.

- [ ] **Step 6: Commit**

```bash
git add src/teams_transcriber/ui/live_transcript_view.py tests/ui/test_live_transcript_view.py
git commit -m "feat(ui): rewrite transcript view as a selectable smooth-scroll document"
```

---

## FEATURE B — Deferred processing while notes are open

### Task B1: Add the `WAITING_FOR_NOTES` status

**Files:**
- Modify: `src/teams_transcriber/storage/models.py:18-25`
- Test: `tests/test_storage.py` (or `tests/test_pipeline_defer.py`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_defer.py
from teams_transcriber.storage.models import RecordingStatus


def test_waiting_for_notes_status_exists():
    assert RecordingStatus.WAITING_FOR_NOTES.value == "waiting_for_notes"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_pipeline_defer.py::test_waiting_for_notes_status_exists -v`
Expected: FAIL (AttributeError).

- [ ] **Step 3: Add the enum value**

In `models.py` `RecordingStatus`, after `SUMMARIZING`:
```python
    WAITING_FOR_NOTES = "waiting_for_notes"
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_pipeline_defer.py::test_waiting_for_notes_status_exists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/storage/models.py tests/test_pipeline_defer.py
git commit -m "feat(storage): add WAITING_FOR_NOTES recording status"
```

### Task B2: Pipeline gate — defer / release / submit extraction

**Files:**
- Modify: `src/teams_transcriber/pipeline.py:36-65,84-103,149-151,205-245`
- Test: `tests/test_pipeline_defer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pipeline_defer.py (add to the file)
import threading
from teams_transcriber.pipeline import Pipeline
from teams_transcriber.events import EventBus, RecordingFinalized
from teams_transcriber.storage import Database, RecordingRepo, RecordingStatus
from teams_transcriber.storage.models import Recording, RecordingSource


def _make_pipeline(tmp_path, *, gate):
    # Reuse the project's pipeline test fixtures if present; otherwise build
    # minimal collaborators. See tests/test_pipeline.py for the canonical setup.
    from teams_transcriber.config import load_settings
    from teams_transcriber.paths import AppPaths
    paths = AppPaths(root=tmp_path)
    db = Database(paths.db_path)
    settings = load_settings(paths)
    pipe = Pipeline(
        bus=EventBus(), db=db, paths=paths, settings=settings,
        audio_source_factory=lambda: (_ for _ in ()).throw(AssertionError("unused")),
        meeting_watcher=None,
        processing_gate=gate,
    )
    return pipe, db


def _insert_recording(db) -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-26T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="t", display_title=None,
        audio_path="x.opus", audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.TRANSCRIBING, error_message=None,
    ))
    return rec.id


def test_finalized_defers_when_gate_true(tmp_path, monkeypatch):
    pipe, db = _make_pipeline(tmp_path, gate=lambda rid: True)
    submitted = []
    monkeypatch.setattr(pipe, "_submit_post_processing", lambda rid: submitted.append(rid))
    rid = _insert_recording(db)
    pipe._on_recording_finalized(RecordingFinalized(recording_id=rid, duration_ms=1000))
    assert submitted == []  # deferred, not submitted
    assert RecordingRepo(db).get(rid).status == RecordingStatus.WAITING_FOR_NOTES
    assert rid in pipe._deferred


def test_finalized_submits_when_gate_false(tmp_path, monkeypatch):
    pipe, db = _make_pipeline(tmp_path, gate=lambda rid: False)
    submitted = []
    monkeypatch.setattr(pipe, "_submit_post_processing", lambda rid: submitted.append(rid))
    rid = _insert_recording(db)
    pipe._on_recording_finalized(RecordingFinalized(recording_id=rid, duration_ms=1000))
    assert submitted == [rid]
    assert rid not in pipe._deferred


def test_release_processing_submits_and_clears(tmp_path, monkeypatch):
    pipe, db = _make_pipeline(tmp_path, gate=lambda rid: True)
    submitted = []
    monkeypatch.setattr(pipe, "_submit_post_processing", lambda rid: submitted.append(rid))
    rid = _insert_recording(db)
    pipe._on_recording_finalized(RecordingFinalized(recording_id=rid, duration_ms=1000))
    pipe.release_processing(rid)
    assert submitted == [rid]
    assert rid not in pipe._deferred
    assert RecordingRepo(db).get(rid).status == RecordingStatus.TRANSCRIBING


def test_release_unknown_id_is_noop(tmp_path, monkeypatch):
    pipe, db = _make_pipeline(tmp_path, gate=lambda rid: True)
    submitted = []
    monkeypatch.setattr(pipe, "_submit_post_processing", lambda rid: submitted.append(rid))
    pipe.release_processing(9999)
    assert submitted == []
```

> Adapt `_make_pipeline` to the real fixtures in `tests/test_pipeline.py`
> (e.g. it may already provide a `pipeline` fixture, a `FakeTranscriber`, and a
> `FakeSummarizer`). The behavioral asserts are what matter.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_pipeline_defer.py -v`
Expected: FAIL (`processing_gate` kwarg unknown; `_deferred`,
`_submit_post_processing`, `release_processing` undefined).

- [ ] **Step 3: Implement the gate**

In `pipeline.py` `__init__` signature add the param and state:
```python
    def __init__(
        self,
        *,
        bus: EventBus,
        db: Database,
        paths: AppPaths,
        settings: Settings,
        audio_source_factory: Callable[[], AudioSource],
        meeting_watcher: MeetingWatcher | None = None,
        transcriber: Transcriber | None = None,
        summarizer: Summarizer | None = None,
        processing_gate: Callable[[int], bool] | None = None,
    ) -> None:
        ...
        self._processing_gate = processing_gate
        self._deferred: dict[int, RecordingFinalized] = {}
        self._defer_lock = threading.Lock()
```

Extract the submit helper and rewrite the handler:
```python
    def _submit_post_processing(self, recording_id: int) -> None:
        future = self._executor.submit(self._run_post_processing, recording_id)
        self._pending_futures.append(future)

    def _on_recording_finalized(self, evt: RecordingFinalized) -> None:
        if self._processing_gate is not None and self._processing_gate(evt.recording_id):
            RecordingRepo(self._db).update_status(
                evt.recording_id, RecordingStatus.WAITING_FOR_NOTES,
            )
            with self._defer_lock:
                self._deferred[evt.recording_id] = evt
            logger.info("deferring post-processing for %d (notes window open)", evt.recording_id)
            return
        self._submit_post_processing(evt.recording_id)

    def release_processing(self, recording_id: int) -> None:
        """Resume deferred post-processing (called when the notes window closes)."""
        with self._defer_lock:
            evt = self._deferred.pop(recording_id, None)
        if evt is None:
            return
        RecordingRepo(self._db).update_status(recording_id, RecordingStatus.TRANSCRIBING)
        self._submit_post_processing(recording_id)
```

Also update `retry_transcription` (line 101-102) to use the helper:
```python
        self._submit_post_processing(recording_id)
```
(replacing its inline `self._executor.submit(...)` + append).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_pipeline_defer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/pipeline.py tests/test_pipeline_defer.py
git commit -m "feat(pipeline): UI-agnostic processing gate + release_processing"
```

### Task B3: Recover WAITING_FOR_NOTES rows on startup

**Files:**
- Modify: `src/teams_transcriber/pipeline.py:205-245` (`_recover_stuck_recordings`)
- Test: `tests/test_pipeline_defer.py`

- [ ] **Step 1: Write the failing test**

```python
def test_recovery_processes_waiting_rows(tmp_path, monkeypatch):
    # Build pipeline WITHOUT triggering recovery first, then insert a waiting
    # row and call recovery directly.
    pipe, db = _make_pipeline(tmp_path, gate=lambda rid: False)
    submitted = []
    monkeypatch.setattr(pipe, "_submit_post_processing", lambda rid: submitted.append(rid))
    rid = _insert_recording(db)
    RecordingRepo(db).update_status(rid, RecordingStatus.WAITING_FOR_NOTES)
    pipe._recover_stuck_recordings()
    assert submitted == [rid]
    assert RecordingRepo(db).get(rid).status == RecordingStatus.TRANSCRIBING
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_pipeline_defer.py::test_recovery_processes_waiting_rows -v`
Expected: FAIL (waiting rows are ignored by recovery).

- [ ] **Step 3: Add the recovery branch**

In `_recover_stuck_recordings`, after the SUMMARIZING and TRANSCRIBING loops,
add (the import for `RecordingStatus` is already at module top):
```python
        for rec in rec_repo.list_by_status(RecordingStatus.WAITING_FOR_NOTES):
            if rec.id is None:
                continue
            # No notes window can be open at startup — process it now.
            logger.info("recover: %d was waiting for notes, resuming", rec.id)
            rec_repo.update_status(rec.id, RecordingStatus.TRANSCRIBING)
            self._submit_post_processing(rec.id)
```

> Note: `_recover_stuck_recordings` runs inside `__init__`. The first test in
> B2 constructs a pipeline (which calls recovery) before inserting rows, so
> existing tests are unaffected; this test calls recovery explicitly.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_pipeline_defer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/pipeline.py tests/test_pipeline_defer.py
git commit -m "feat(pipeline): recover WAITING_FOR_NOTES rows on startup"
```

### Task B4: WorkspaceWindow waiting footer

**Files:**
- Modify: `src/teams_transcriber/ui/workspace_window.py:148-163,217-225`
- Test: `tests/ui/test_workspace_window.py`

- [ ] **Step 1: Write the failing test**

```python
def test_show_waiting_for_processing_reveals_footer_note(qapp, workspace_db):
    from teams_transcriber.ui.workspace_window import WorkspaceWindow
    from teams_transcriber.ui.qt_bridge import QtEventBridge
    from teams_transcriber.events import EventBus
    win = WorkspaceWindow(db=workspace_db.db, recording_id=workspace_db.recording_id,
                          bridge=QtEventBridge(EventBus()), live=False)
    assert not win._waiting_label.isVisible() or win._waiting_label.text() == ""
    win.show_waiting_for_processing()
    assert "close this window" in win._waiting_label.text().lower()
```

> `workspace_db` fixture mirrors the existing one in
> `tests/ui/test_workspace_window.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ui/test_workspace_window.py::test_show_waiting_for_processing_reveals_footer_note -v`
Expected: FAIL (`_waiting_label` / `show_waiting_for_processing` undefined).

- [ ] **Step 3: Add the waiting label + method**

In `WorkspaceWindow.__init__`, in the footer block (before the stretch /
buttons), add a hidden label:
```python
        self._waiting_label = QLabel("")
        self._waiting_label.setStyleSheet("color: #B45309; font-size: 12px;")
        footer.addWidget(self._waiting_label)
        footer.addStretch(1)
```
(Move the existing `footer.addStretch(1)` to come *after* the waiting label so
the label sits on the left.)

Add the method:
```python
    def show_waiting_for_processing(self) -> None:
        """Indicate that transcription/summarization is paused until close."""
        self._waiting_label.setText("⏳ Transcription will start when you close this window.")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ui/test_workspace_window.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/workspace_window.py tests/ui/test_workspace_window.py
git commit -m "feat(ui): workspace footer note while processing is deferred"
```

### Task B5: App wiring — gate predicate, waiting UI, release on close

**Files:**
- Modify: `src/teams_transcriber/ui/app.py:101-107,388-403,633-636`
- Test: covered by manual verification + the pipeline tests (App wiring is
  glue; add a light unit test for the predicate).

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_app_defer.py
def test_should_defer_predicate_tracks_open_workspaces(qapp, app_instance):
    # app_instance: a constructed App (see how tests/ui build one; if no such
    # fixture exists, this can be a thin object exercising the predicate).
    app = app_instance
    assert app._should_defer_processing(123) is False
    app._mark_workspace_open(123)
    assert app._should_defer_processing(123) is True
    app._mark_workspace_closed(123)
    assert app._should_defer_processing(123) is False
```

> If constructing a full `App` in tests is impractical (it builds a tray,
> pipeline, etc.), instead factor the set + lock + three methods into a tiny
> helper class `_WorkspaceTracker` in `app.py` and unit-test that class in
> isolation. Prefer whichever keeps the test honest and fast.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ui/test_app_defer.py -v`
Expected: FAIL (methods undefined).

- [ ] **Step 3: Implement the predicate + tracking + wiring**

In `App.__init__`, before constructing the pipeline, add the tracker state:
```python
        import threading as _threading
        self._open_workspace_ids: set[int] = set()
        self._open_workspace_lock = _threading.Lock()
```
Add helper methods on `App`:
```python
    def _mark_workspace_open(self, recording_id: int) -> None:
        with self._open_workspace_lock:
            self._open_workspace_ids.add(recording_id)

    def _mark_workspace_closed(self, recording_id: int) -> None:
        with self._open_workspace_lock:
            self._open_workspace_ids.discard(recording_id)

    def _should_defer_processing(self, recording_id: int) -> bool:
        with self._open_workspace_lock:
            return recording_id in self._open_workspace_ids
```
Pass the predicate into the pipeline (line 101-107):
```python
        self.pipeline = Pipeline(
            bus=self.bus, db=self.db, paths=self.paths, settings=self.settings,
            audio_source_factory=audio_factory,
            meeting_watcher=watcher,
            transcriber=Transcriber(bus=self.bus, db=self.db, settings=self.settings),
            summarizer=Summarizer(bus=self.bus, db=self.db, settings=self.settings),
            processing_gate=self._should_defer_processing,
        )
```
In `_open_workspace` (after `self._workspace_windows[recording_id] = win`):
```python
        self._mark_workspace_open(recording_id)
```
In `_on_workspace_closed`:
```python
    def _on_workspace_closed(self, recording_id: int) -> None:
        windows = getattr(self, "_workspace_windows", {})
        windows.pop(recording_id, None)
        self._mark_workspace_closed(recording_id)
        # If this recording was waiting on notes, resume + reflect in the UI.
        rec = RecordingRepo(self.db).get(recording_id)
        was_waiting = rec is not None and rec.status == RecordingStatus.WAITING_FOR_NOTES
        self.pipeline.release_processing(recording_id)
        if was_waiting:
            self.tray.set_state(TrayState.PROCESSING)
            self.active_banner.set_processing()
            show_in_app_toast(
                "Processing started",
                "Transcribing and summarizing your meeting now.",
            )
        self._refresh_history()
```
Rewrite `_on_recording_finalized` (line 388-403) to branch on the predicate:
```python
    def _on_recording_finalized(self, _evt: RecordingFinalized) -> None:
        rid = self._active_recording_id
        self._active_recording_id = None
        deferred = rid is not None and self._should_defer_processing(rid)
        workspaces = getattr(self, "_workspace_windows", {})
        ws = workspaces.get(rid) if rid is not None else None
        if ws is not None:
            ws.set_recording_finished()
        if deferred:
            self.tray.set_state(TrayState.IDLE)
            self.active_banner.hide_banner()
            if ws is not None:
                ws.show_waiting_for_processing()
            show_in_app_toast(
                "Waiting for notes",
                "Transcription will start when you close the notes window.",
            )
        else:
            self.tray.set_state(TrayState.PROCESSING)
            self.active_banner.set_processing()
            show_in_app_toast(
                "Recording stopped",
                "Transcribing and summarizing — you'll get a notification when it's ready.",
            )
        self._update_record_button()
        self._refresh_history()
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `uv run pytest tests/ui/test_app_defer.py -v`
Expected: PASS
Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py tests/ui/test_app_defer.py
git commit -m "feat(app): defer processing while a notes window is open"
```

---

## FEATURE A — Shared window chrome

### Task A1: FramelessWindowMixin

**Files:**
- Create: `src/teams_transcriber/ui/frameless.py`
- Test: `tests/ui/test_frameless.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/ui/test_frameless.py
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QFrame, QWidget
from teams_transcriber.ui.frameless import FramelessWindowMixin


class _Win(FramelessWindowMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.resize(400, 300)
        frame = QFrame(self)
        self._init_frameless(frame)


def test_edge_detection_corners_and_edges(qapp):
    w = _Win()
    w.resize(400, 300)
    assert w._edge_at(QPoint(2, 2)) == (Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
    assert w._edge_at(QPoint(398, 298)) == (Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
    assert w._edge_at(QPoint(2, 150)) == Qt.Edge.LeftEdge
    assert w._edge_at(QPoint(200, 298)) == Qt.Edge.BottomEdge
    assert int(w._edge_at(QPoint(200, 150))) == 0   # interior


def test_resizable_false_disables_edges(qapp):
    class _Fixed(FramelessWindowMixin, QWidget):
        def __init__(self):
            super().__init__()
            self.resize(400, 300)
            self._init_frameless(QFrame(self), resizable=False)
    w = _Fixed()
    assert int(w._edge_at(QPoint(2, 2))) == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/ui/test_frameless.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the mixin**

```python
"""Reusable frameless-window chrome: drag-move (via TitleBar), edge resize,
rounded corners that square off when maximized.

Host requirements:
  * be a QWidget subclass with FramelessWindowHint set and
    WA_TranslucentBackground enabled,
  * build an outer QFrame (objectName 'OuterFrame') as its only top-level
    child and call self._init_frameless(outer_frame),
  * give the TitleBar's maximize_requested signal to self.toggle_max and
    minimize/close to showMinimized/close.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QCursor, QMouseEvent

from teams_transcriber.ui.theme import COLORS, RADIUS

_RESIZE_MARGIN: int = 6


class FramelessWindowMixin:
    _outer = None            # type: ignore[var-annotated]
    _resizable: bool = True
    _title_bar = None        # type: ignore[var-annotated]

    def _init_frameless(self, outer, *, resizable: bool = True, title_bar=None) -> None:
        self._outer = outer
        self._resizable = resizable
        self._title_bar = title_bar
        outer.setObjectName("OuterFrame")
        self.setMouseTracking(True)          # type: ignore[attr-defined]
        outer.setMouseTracking(True)
        self._apply_outer_style(maximized=False)

    def _apply_outer_style(self, *, maximized: bool) -> None:
        radius = 0 if maximized else RADIUS["window"]
        self._outer.setStyleSheet(
            f"#OuterFrame {{ background: {COLORS['bg']}; border-radius: {radius}px; }}"
        )

    def toggle_max(self) -> None:
        if self.isMaximized():               # type: ignore[attr-defined]
            self.showNormal()                # type: ignore[attr-defined]
            self._apply_outer_style(maximized=False)
            if self._title_bar is not None:
                self._title_bar.set_maximized(False)
        else:
            self.showMaximized()             # type: ignore[attr-defined]
            self._apply_outer_style(maximized=True)
            if self._title_bar is not None:
                self._title_bar.set_maximized(True)

    def _edge_at(self, pos: QPoint):
        edges = Qt.Edges()
        if not self._resizable or self.isMaximized():   # type: ignore[attr-defined]
            return edges
        rect = self.rect()                               # type: ignore[attr-defined]
        if pos.x() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= rect.width() - _RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= rect.height() - _RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _cursor_for_edges(self, edges) -> Qt.CursorShape:
        if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge) and edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            if (edges & Qt.Edge.LeftEdge and edges & Qt.Edge.TopEdge) or \
               (edges & Qt.Edge.RightEdge and edges & Qt.Edge.BottomEdge):
                return Qt.CursorShape.SizeFDiagCursor
            return Qt.CursorShape.SizeBDiagCursor
        if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            return Qt.CursorShape.SizeHorCursor
        if edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        edges = self._edge_at(e.position().toPoint())
        self.setCursor(QCursor(self._cursor_for_edges(edges)))  # type: ignore[attr-defined]
        super().mouseMoveEvent(e)                               # type: ignore[misc]

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            edges = self._edge_at(e.position().toPoint())
            if edges:
                handle = self.windowHandle()                    # type: ignore[attr-defined]
                if handle is not None:
                    handle.startSystemResize(edges)
                    e.accept()
                    return
        super().mousePressEvent(e)                              # type: ignore[misc]

    def leaveEvent(self, e: QEvent) -> None:
        self.unsetCursor()                                      # type: ignore[attr-defined]
        super().leaveEvent(e)                                   # type: ignore[misc]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ui/test_frameless.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/frameless.py tests/ui/test_frameless.py
git commit -m "feat(ui): reusable FramelessWindowMixin (move + edge resize)"
```

### Task A2: Configurable TitleBar

**Files:**
- Modify: `src/teams_transcriber/ui/title_bar.py`
- Test: `tests/ui/test_frameless.py`

- [ ] **Step 1: Write failing tests**

```python
def test_titlebar_builds_only_requested_controls(qapp):
    from teams_transcriber.ui.title_bar import TitleBar
    tb = TitleBar(title="X", controls=("close",))
    assert tb.title_label.text() == "X"
    assert tb.close_btn is not None
    assert tb.minimize_btn is None
    assert tb.maximize_btn is None
    assert tb.settings_btn is None


def test_titlebar_full_controls(qapp):
    from teams_transcriber.ui.title_bar import TitleBar
    tb = TitleBar(title="Main", controls=("settings", "min", "max", "close"))
    assert tb.settings_btn is not None
    assert tb.minimize_btn is not None
    assert tb.maximize_btn is not None
    assert tb.close_btn is not None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/ui/test_frameless.py -k titlebar -v`
Expected: FAIL (TitleBar takes no `title`/`controls`; always builds all buttons).

- [ ] **Step 3: Make TitleBar configurable**

Rewrite `title_bar.py` constructor:
```python
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "Teams Transcriber",
        controls: tuple[str, ...] = ("settings", "min", "max", "close"),
        extra_left: list[QWidget] | None = None,
        extra_right: list[QWidget] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setObjectName("TitleBar")
        self._drag_anchor: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(8)

        for w in (extra_left or []):
            layout.addWidget(w)

        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "subtitle")
        layout.addWidget(self.title_label)
        layout.addStretch(1)

        for w in (extra_right or []):
            layout.addWidget(w)

        self.settings_btn = None
        self.minimize_btn = None
        self.maximize_btn = None
        self.close_btn = None
        if "settings" in controls:
            self.settings_btn = self._make_btn(IconName.SETTINGS, self.settings_requested.emit)
            layout.addWidget(self.settings_btn)
        if "min" in controls:
            self.minimize_btn = self._make_btn(IconName.MINIMIZE, self.minimize_requested.emit)
            layout.addWidget(self.minimize_btn)
        if "max" in controls:
            self.maximize_btn = self._make_btn(IconName.MAXIMIZE, self.maximize_requested.emit)
            layout.addWidget(self.maximize_btn)
        if "close" in controls:
            self.close_btn = self._make_btn(IconName.CLOSE, self.close_requested.emit)
            layout.addWidget(self.close_btn)
```
Guard `set_maximized` for the no-max case:
```python
    def set_maximized(self, maximized: bool) -> None:
        if self.maximize_btn is not None:
            self.maximize_btn.setIcon(get_icon(IconName.RESTORE if maximized else IconName.MAXIMIZE))
```
Guard `mouseDoubleClickEvent` so non-resizable/no-max windows don't toggle:
```python
    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        del e
        if self.maximize_btn is not None:
            self.maximize_requested.emit()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ui/test_frameless.py -k titlebar -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/title_bar.py tests/ui/test_frameless.py
git commit -m "feat(ui): configurable TitleBar (controls + extras + title)"
```

### Task A3: MainWindow consumes the mixin

**Files:**
- Modify: `src/teams_transcriber/ui/main_window.py`
- Test: existing main-window behavior (add a smoke test).

- [ ] **Step 1: Write the smoke test**

```python
# tests/ui/test_main_window.py
def test_main_window_uses_shared_chrome(qapp):
    from teams_transcriber.ui.main_window import MainWindow
    from teams_transcriber.ui.frameless import FramelessWindowMixin
    w = MainWindow()
    assert isinstance(w, FramelessWindowMixin)
    assert w.title_bar.settings_btn is not None  # main keeps the settings cog
    # toggle_max flips style without raising
    w.toggle_max()
    w.toggle_max()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ui/test_main_window.py -v`
Expected: FAIL (MainWindow is not a FramelessWindowMixin yet).

- [ ] **Step 3: Refactor MainWindow**

- Class header: `class MainWindow(FramelessWindowMixin, QMainWindow):`
  (import `FramelessWindowMixin` from `teams_transcriber.ui.frameless`).
- Delete the inline `_edge_at`, `_cursor_for_edges`, `mouseMoveEvent`,
  `mousePressEvent`, `leaveEvent`, `_apply_outer_style`, `_toggle_max`, and the
  module-level `_RESIZE_MARGIN` (now provided by the mixin).
- After building `outer` and `self.title_bar`, call:
  ```python
  self.title_bar = TitleBar(controls=("settings", "min", "max", "close"))
  self.title_bar.minimize_requested.connect(self.showMinimized)
  self.title_bar.maximize_requested.connect(self.toggle_max)
  self.title_bar.close_requested.connect(self.close)
  ...
  self.setCentralWidget(outer)
  self._init_frameless(outer, resizable=True, title_bar=self.title_bar)
  ```
  (Note: `outer` keeps `setObjectName("OuterFrame")`; `_init_frameless` sets it
  too — harmless. Keep `WA_TranslucentBackground` + `FramelessWindowHint` +
  `setMouseTracking(True)` as today.)

- [ ] **Step 4: Run to verify pass + main window tests**

Run: `uv run pytest tests/ui/test_main_window.py tests/ui/test_frameless.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/main_window.py tests/ui/test_main_window.py
git commit -m "refactor(ui): MainWindow uses FramelessWindowMixin"
```

### Task A4: WorkspaceWindow — shared chrome + resize

**Files:**
- Modify: `src/teams_transcriber/ui/workspace_window.py`
- Test: `tests/ui/test_workspace_window.py`

- [ ] **Step 1: Write failing test**

```python
def test_workspace_is_resizable_with_shared_chrome(qapp, workspace_db):
    from teams_transcriber.ui.workspace_window import WorkspaceWindow
    from teams_transcriber.ui.frameless import FramelessWindowMixin
    from teams_transcriber.ui.qt_bridge import QtEventBridge
    from teams_transcriber.events import EventBus
    from PySide6.QtCore import QPoint, Qt
    win = WorkspaceWindow(db=workspace_db.db, recording_id=workspace_db.recording_id,
                          bridge=QtEventBridge(EventBus()), live=False)
    win.resize(900, 600)
    assert isinstance(win, FramelessWindowMixin)
    assert win._edge_at(QPoint(2, 2)) == (Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
    # always-on-top toggle still present
    assert win._pin_btn is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ui/test_workspace_window.py::test_workspace_is_resizable_with_shared_chrome -v`
Expected: FAIL.

- [ ] **Step 3: Refactor WorkspaceWindow**

- Class header: `class WorkspaceWindow(FramelessWindowMixin, QWidget):`.
- Remove `_WorkspaceTitleBar`; build the status dot (📌-pin) and recording-dot
  as `extra_*` widgets for the shared `TitleBar`:
  ```python
  self._dot = QLabel("●")
  self._dot.setStyleSheet("color: #9CA3AF; font-size: 14px;")
  self._pin_btn = QPushButton("📌")
  self._pin_btn.setCheckable(True)
  self._pin_btn.setProperty("role", "ghost")
  self._pin_btn.setFixedSize(28, 28)
  self._pin_btn.setToolTip("Always on top")
  self._pin_btn.toggled.connect(self._on_always_on_top)

  self._title_bar = TitleBar(
      title=title,
      controls=("min", "max", "close"),
      extra_left=[self._dot],
      extra_right=[self._pin_btn],
  )
  self._title_bar.minimize_requested.connect(self.showMinimized)
  self._title_bar.maximize_requested.connect(self.toggle_max)
  self._title_bar.close_requested.connect(self.close)
  ```
  Replace `_WorkspaceTitleBar.set_recording` usage: add a helper on the window:
  ```python
  def _set_recording_dot(self, recording: bool) -> None:
      color = "#EF4444" if recording else "#9CA3AF"
      self._dot.setStyleSheet(f"color: {color}; font-size: 14px;")
  ```
  Call `self._set_recording_dot(live)` after building, and in
  `set_recording_finished()` call `self._set_recording_dot(False)` (replacing
  the old `self._title_bar.set_recording(False)`).
- Standardize chrome to match MainWindow: set `WA_TranslucentBackground` True,
  make the outer layout margins `0`, drop the `QGraphicsDropShadowEffect`, name
  the frame `OuterFrame` with the themed background, and call
  `self._init_frameless(self._frame, resizable=True, title_bar=self._title_bar)`
  at the end of `__init__`. Keep `FramelessWindowHint | Window` flags.
  ```python
  self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
  self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
  self.setMouseTracking(True)
  self.resize(1100, 700)
  self._frame = QFrame()
  self._frame.setObjectName("OuterFrame")
  outer = QVBoxLayout(self)
  outer.setContentsMargins(0, 0, 0, 0)
  outer.addWidget(self._frame)
  inner = QVBoxLayout(self._frame)
  inner.setContentsMargins(0, 0, 0, 0)
  inner.setSpacing(0)
  inner.addWidget(self._title_bar)
  ... (splitter + footer unchanged) ...
  self._init_frameless(self._frame, resizable=True, title_bar=self._title_bar)
  ```
- `_on_always_on_top` is unchanged. `closeEvent` is unchanged (still emits
  `closed`).

> The `_pin_btn.toggled` path calls `setWindowFlags(... StaysOnTop ...)` then
> `self.show()`. That still works on the QWidget host.

- [ ] **Step 4: Run to verify pass + workspace tests**

Run: `uv run pytest tests/ui/test_workspace_window.py -v`
Expected: PASS (update any test that referenced `_WorkspaceTitleBar` or
`_title_bar.set_recording`).

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/workspace_window.py tests/ui/test_workspace_window.py
git commit -m "feat(ui): WorkspaceWindow shared chrome + edge resize"
```

### Task A5: TranscriptWindow — shared chrome + resize

**Files:**
- Modify: `src/teams_transcriber/ui/transcript_window.py`
- Test: `tests/ui/test_transcript_window.py`

- [ ] **Step 1: Write failing test**

```python
# tests/ui/test_transcript_window.py
def test_transcript_window_movable_resizable(qapp, workspace_db):
    from teams_transcriber.ui.transcript_window import TranscriptWindow
    from teams_transcriber.ui.frameless import FramelessWindowMixin
    from PySide6.QtCore import QPoint, Qt
    win = TranscriptWindow(db=workspace_db.db, recording_id=workspace_db.recording_id)
    win.resize(720, 600)
    assert isinstance(win, FramelessWindowMixin)
    assert win._edge_at(QPoint(2, 2)) == (Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
    assert win._title_bar.close_btn is not None
    assert win._title_bar.maximize_btn is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ui/test_transcript_window.py -v`
Expected: FAIL.

- [ ] **Step 3: Refactor TranscriptWindow**

- Class header: `class TranscriptWindow(FramelessWindowMixin, QWidget):`.
- Replace the manual title row + close button with the shared `TitleBar`:
  ```python
  self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
  self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
  self.setMouseTracking(True)
  self.resize(720, 600)

  frame = QFrame()
  frame.setObjectName("OuterFrame")
  outer = QVBoxLayout(self)
  outer.setContentsMargins(0, 0, 0, 0)
  outer.addWidget(frame)

  inner = QVBoxLayout(frame)
  inner.setContentsMargins(0, 0, 0, 0)
  inner.setSpacing(0)

  rec = RecordingRepo(db).get(recording_id)
  title_text = (rec.display_title if rec else None) or "Transcript"
  self._title_bar = TitleBar(title=title_text, controls=("min", "max", "close"))
  self._title_bar.minimize_requested.connect(self.showMinimized)
  self._title_bar.maximize_requested.connect(self.toggle_max)
  self._title_bar.close_requested.connect(self.close)
  inner.addWidget(self._title_bar)

  body = QVBoxLayout()
  body.setContentsMargins(16, 8, 16, 16)
  self.transcript_view = LiveTranscriptView()
  self.transcript_view.load_segments(TranscriptRepo(db).list_for_recording(recording_id))
  body.addWidget(self.transcript_view, 1)
  inner.addLayout(body)

  self._init_frameless(frame, resizable=True, title_bar=self._title_bar)
  ```
- Drop the `QGraphicsDropShadowEffect` import/usage.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ui/test_transcript_window.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/transcript_window.py tests/ui/test_transcript_window.py
git commit -m "feat(ui): TranscriptWindow shared chrome + edge resize"
```

### Task A6: Dialogs adopt themed frameless chrome (Settings, Wizard, Update)

**Files:**
- Modify: `src/teams_transcriber/ui/settings_dialog.py`,
  `first_run_wizard.py`, `update_dialog.py`
- Test: `tests/ui/test_settings_dialog.py` (+ smoke for wizard/update)

These are currently native-chrome `QDialog`s. Convert each to the shared
themed chrome. The pattern (apply to each):

1. Class header → `class SettingsDialog(FramelessWindowMixin, QDialog):`.
2. In `__init__`, before building the body:
   ```python
   self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
   self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
   self.setMouseTracking(True)
   ```
3. Build the outer frame + shared TitleBar and nest the existing body inside:
   ```python
   frame = QFrame()
   frame.setObjectName("OuterFrame")
   shell = QVBoxLayout(self)
   shell.setContentsMargins(0, 0, 0, 0)
   shell.addWidget(frame)

   inner = QVBoxLayout(frame)
   inner.setContentsMargins(0, 0, 0, 0)
   inner.setSpacing(0)

   self._title_bar = TitleBar(title="Settings", controls=("max", "close"))
   self._title_bar.maximize_requested.connect(self.toggle_max)
   self._title_bar.close_requested.connect(self.reject)
   inner.addWidget(self._title_bar)

   body = QWidget()
   body_layout = QVBoxLayout(body)
   body_layout.setContentsMargins(16, 12, 16, 16)
   # ... move the existing tabs + button box into body_layout ...
   inner.addWidget(body, 1)

   self._init_frameless(frame, resizable=True, title_bar=self._title_bar)
   ```
   For `SettingsDialog`: move the `self._tabs` + `QDialogButtonBox` block into
   `body_layout` (instead of the current top-level `outer`). Keep
   `setWindowTitle("Settings")` for the taskbar text. Keep `resize(700, 540)`.
4. `FirstRunWizard`: TitleBar `title="Welcome"`, `controls=("close",)`,
   `close_requested.connect(self.reject)`. Wrap its existing page stack/body
   into the inner body layout. (Wizard is `resizable=True`.)
5. `UpdateDialog`: TitleBar `title="Update"`, `controls=("close",)`,
   `close_requested.connect(self.reject)`. Wrap its progress/body widgets.

- [ ] **Step 1: Write failing tests**

```python
# tests/ui/test_settings_dialog.py
def test_settings_dialog_has_shared_chrome(qapp, settings_paths):
    from teams_transcriber.ui.settings_dialog import SettingsDialog
    from teams_transcriber.ui.frameless import FramelessWindowMixin
    settings, paths = settings_paths
    dlg = SettingsDialog(settings, paths)
    assert isinstance(dlg, FramelessWindowMixin)
    assert dlg._title_bar.close_btn is not None
    assert dlg._tabs.count() >= 7  # General..About preserved
```
(Add equivalent 3-line smoke tests asserting `isinstance(..., FramelessWindowMixin)`
for `FirstRunWizard` and `UpdateDialog` in their existing test files; create
the test files if absent.)

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/ui/test_settings_dialog.py -v`
Expected: FAIL.

- [ ] **Step 3: Apply the pattern to all three dialogs**

Implement steps 1–5 above for each dialog. Read each file fully first and move
its existing body widgets into the new `body_layout` without changing their
construction/wiring (only the surrounding chrome changes). Verify the
`QDialogButtonBox` Ok/Cancel still connect to `_on_accept`/`reject`.

- [ ] **Step 4: Run to verify pass + full suite**

Run: `uv run pytest tests/ui/test_settings_dialog.py -v`
Expected: PASS
Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/settings_dialog.py src/teams_transcriber/ui/first_run_wizard.py src/teams_transcriber/ui/update_dialog.py tests/ui/test_settings_dialog.py
git commit -m "feat(ui): themed frameless chrome for Settings/Wizard/Update dialogs"
```

---

## Final verification

- [ ] **Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (≥ 283 baseline + new Phase 9 tests).

- [ ] **Manual smoke (per the spec's Manual section)**

Launch the app (scrub the proxy env per CLAUDE.md):
`env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy uv run python -m teams_transcriber`
- Resize/move every window from edges + corners; min/max/close work; corners
  square when maximized.
- Manual recording with the workspace open → "Waiting for notes" toast +
  footer note → close workspace → "Processing started" → summary generates
  with the notes included.
- Tick todos; the history chip recolors red → amber → green live and shows
  "N todos | M complete".
- Long transcript: smooth scroll; drag-select across many lines; copy.

- [ ] **Update memory + spec status, then finish the branch**

Per `superpowers:finishing-a-development-branch`: update
`memory/project_teams_transcriber.md` with the Phase 9 summary, mark the spec
Status line done, and present merge/PR options.

---

## Self-review notes (author)

- Spec coverage: Feature 1 (chrome) → A1–A6; Feature 2 (defer) → B1–B5;
  Feature 3a (todo chip) → C1–C3; Feature 4 (transcript) → D1; Feature 5
  (selectable) → E1. All spec sections map to tasks.
- Deviation from spec: no `ProcessingDeferred` event (spec amended to the
  shared-predicate design in this revision).
- Type consistency: history rows are `(Recording, str|None, int, int)`
  everywhere (`set_recordings`, `filter_for_bucket`, `_refresh_history`);
  `MeetingCard(..., todos_done=...)`; `processing_gate: Callable[[int], bool]`;
  `release_processing(recording_id: int)`.
