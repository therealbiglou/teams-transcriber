# Phase 10 — PDF Export + Master To-Do List

**Date:** 2026-05-27
**Status:** Approved (Brian, 2026-05-27)
**Branch:** `feature/phase-10-pdf-export-master-todos` (off Phase 9)

## Goals

Two features from Brian's testing feedback:

1. **Export a summary to PDF** — folded into the existing Export action as a
   third format alongside Markdown and plain text.
2. **A master to-do list view** — a sidebar "Todos" section that swaps the
   main panel to a list of every to-do across all meetings, grouped by
   meeting, with interactive checkboxes and a "Go to summary" jump button.

## Decisions locked with Brian (2026-05-27)

- **PDF lives in the existing Export menu** (format choice `.md` / `.txt` /
  `.pdf`), not a separate button.
- **The master to-do list is an integrated view**: a new "Todos" section in
  the left sidebar, below History. Clicking it swaps the main interface to the
  master list. Picking a History bucket switches back.
- **Checkboxes are interactive and sync everywhere**: toggling in the master
  list updates the stored `TodoState`, the summary pane, and the Phase-9
  history completion chip.

## Feature 1 — PDF export

### Current state

`SummaryPane` has an "Export" button (`export_requested` signal) wired to
`App._export_summary` (`app.py`), which opens a `QFileDialog` filtered to
`Markdown (*.md);;Plain text (*.txt)` and writes a markdown string built
inline. `SummaryPane._copy_markdown` builds a *second*, nearly identical
markdown string for the clipboard. This duplication is consolidated as part
of this work.

### New module: `src/teams_transcriber/summary_export.py`

Pure serialization, no Qt — easy to unit-test:

```python
def to_markdown(summary: Summary, recording: Recording,
                todo_states: dict[int, bool]) -> str: ...
def to_plaintext(summary: Summary, recording: Recording,
                 todo_states: dict[int, bool]) -> str: ...
def to_html(summary: Summary, recording: Recording,
            todo_states: dict[int, bool]) -> str: ...
```

- `todo_states` maps `todo_index → done` (from `TodoStateRepo`). Todos render
  as `- [x]` / `- [ ]` (markdown) or `☑ / ☐` (text) or styled list items
  (HTML), reflecting completion.
- Content for all three: title, `date · duration · model`, summary body,
  My todos (with done state), Action items for others, Key decisions,
  Follow-ups, Topics, and My notes (notes are HTML already; for markdown/text
  they are included as-is/stripped — see below).
- HTML uses simple inline styles matching the app palette (emerald headings,
  readable body) so the PDF looks clean. `manual_notes` is already HTML; embed
  it directly in the HTML export, and for markdown/plaintext include a plain
  rendering (strip tags with a minimal helper or `QTextDocument` is NOT used
  here — keep this module Qt-free; use a tiny regex tag-strip for the
  text/markdown notes rendering).

`SummaryPane._copy_markdown` and `App._export_summary` both switch to using
this module (removing the duplicated inline builders).

### Export flow (`App._export_summary`)

- File dialog filter becomes
  `PDF (*.pdf);;Markdown (*.md);;Plain text (*.txt)` (PDF first / default).
- Branch on the chosen file's suffix:
  - `.pdf` → render via Qt (below).
  - `.md` → `to_markdown(...)`.
  - `.txt` → `to_plaintext(...)`.
  - default/unknown → `.md`.
- Default filename: `<slugified-title>-<YYYY-MM-DD>.pdf`.

### PDF rendering (Qt, no new dependency)

In `app.py` (or a thin `ui/pdf_export.py` helper to keep `app.py` lean):

```python
from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrinter

def render_html_to_pdf(html: str, out_path: str) -> None:
    doc = QTextDocument()
    doc.setHtml(html)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(out_path)
    doc.print_(printer)
```

`QtPrintSupport` ships with PySide6. **Packaging note:** verify the PyInstaller
bundle includes `PySide6.QtPrintSupport` and the platform print plugin (add a
hiddenimport/collect if the smoke build can't import it).

## Feature 2 — Master to-do list

### Sidebar changes (`ui/sidebar.py`)

The sidebar currently renders one "History" header + a button per
`SidebarBucket`. Add, below the bucket buttons:

- a "Todos" section header,
- a single "To-Do List" button.

New signal `todos_selected = Signal()`. The sidebar tracks active state as
*either* a bucket *or* the todos item:

- Clicking the To-Do List button → active = todos, highlight it, de-highlight
  buckets, emit `todos_selected`.
- Clicking any bucket → active = that bucket (existing behavior), de-highlight
  the todos button, emit `bucket_selected`.

`active_bucket` keeps returning the last selected bucket (so returning to
History restores the prior filter).

### Main content becomes a stack (`app.py` `_build_main_content`)

Wrap the existing history+summary `body` and a new `MasterTodoView` in a
`QStackedWidget`:

- page 0: existing `body` (history list + summary pane) — unchanged internals.
- page 1: `MasterTodoView`.

Wiring:
- `sidebar.bucket_selected` → switch to page 0, then the existing
  `_on_bucket` filtering.
- `sidebar.todos_selected` → reload the master view, switch to page 1.
- `MasterTodoView.go_to_summary(rid)` → switch sidebar/active back to History
  (bucket `ALL` so the card exists), switch to page 0, `_refresh_history()`,
  `self.history.select(rid)` (selects + shows the summary), and `_show_window()`.
- `MasterTodoView.todo_toggled(rid)` → `_refresh_history()` so the completion
  chip updates. (The master view updates its own checkbox in place.)
- Also reload the master view whenever it's shown and after a summary-pane
  toggle (connect `SummaryPane.todo_state_changed` to a master-view reload too,
  so the two stay consistent).

### New widget: `src/teams_transcriber/ui/master_todo_view.py`

`MasterTodoView(QScrollArea)`:

- `reload()` — rebuild from the DB. For each recording (most recent first)
  that has a `Summary` with ≥1 `my_todos`, render a group:
  - **Header row:** meeting title (display_title or summary.title) + a muted
    date, and a "Go to summary" button (`role="secondary"`) on the right.
  - **Body:** one `QCheckBox` per todo, label = task (+ `(due …)` if set),
    checked from `TodoState`. Toggling calls
    `TodoStateRepo.upsert(rid, idx, task, checked)` then
    `self.todo_toggled.emit(rid)`.
  - Completed todos may be shown with a strikethrough style (optional polish).
- Empty state: a centered muted label "No to-dos yet." when no meeting has
  todos.
- Signals: `go_to_summary = Signal(int)`, `todo_toggled = Signal(int)`.
- Construction takes the `Database`. Follows the SummaryPane/HistoryList
  scroll-area guards (horizontal scrollbar as-needed; cap inner width to
  viewport in `resizeEvent`) to honor the responsive-layout rule.

Data source: `RecordingRepo.list_recent`, `SummaryRepo.get`,
`TodoStateRepo.list_for_recording` — same repos the rest of the UI uses. No
new storage.

## Non-goals (deferred)

- Filtering the master list (hide completed, by date, search) — show all,
  grouped, completed shown in place.
- Reordering / editing todo text in the master list.
- Cross-meeting "due today" smart grouping (group strictly by meeting, as
  requested).
- A dedicated standalone PDF stylesheet/theme beyond clean inline styles.

## Testing

### Unit (Qt-free) — `summary_export`
- `to_markdown` / `to_plaintext` / `to_html` include title, summary, each
  section, and render todo done-state correctly (`[x]` vs `[ ]`).
- Notes tag-strip helper turns simple HTML notes into readable text for
  markdown/plaintext.

### Widget
- `MasterTodoView.reload` builds one group per meeting-with-todos, skips
  meetings with no todos, newest first; empty-state label when none.
- Toggling a checkbox calls `TodoStateRepo.upsert` and emits `todo_toggled`.
- "Go to summary" button emits `go_to_summary(rid)`.
- `Sidebar` emits `todos_selected` on the To-Do List button; emits
  `bucket_selected` and clears the todos highlight on a bucket click.
- `App`: the content `QStackedWidget` switches to the master page on
  `todos_selected` and back to history on `bucket_selected` /
  `go_to_summary`.
- PDF: `render_html_to_pdf` writes a non-empty file ending in `%PDF` to a temp
  path (smoke). `_export_summary` routes a `.pdf` path to the PDF renderer and
  a `.md` path to the markdown writer (assert via the chosen-path branch; the
  `QFileDialog` call is factored so a test can inject the path, or tested by
  calling the format-dispatch helper directly).

### Manual
- Export a summary as PDF; open it; verify formatting + todo checkboxes.
- Click sidebar "To-Do List"; verify grouping, due dates, completion state.
- Tick a todo in the master list → its meeting's history chip recolors; open
  that meeting's summary → the same todo shows checked.
- "Go to summary" returns to History with the meeting selected and shown.

## Risks

| Risk | Mitigation |
|---|---|
| `QtPrintSupport` missing from the PyInstaller bundle | Verify in the smoke build; add hiddenimport/collect-submodules for `PySide6.QtPrintSupport` if needed. |
| Master view rebuild cost with many meetings | Personal-scale data; `reload()` is O(recordings) with a couple of small queries each. Fine. Rebuild only on show / toggle, not per keystroke. |
| Sidebar active-state regressions (bucket vs todos) | Unit-test both signals + that `active_bucket` is preserved when returning to History. |
| Notes HTML embedded in PDF could carry odd markup | `manual_notes` is produced by our own NotesEditor (bounded rich text); embed directly in HTML export; strip tags for md/txt. |
| Duplicated markdown builders drift | Consolidated into `summary_export`; both Copy and Export use it. |
