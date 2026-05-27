# Phase 10 — PDF Export + Master To-Do List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add PDF as a third Export format for summaries, and add a sidebar "Todos" view that lists every to-do grouped by meeting with interactive checkboxes and a jump-to-summary button.

**Architecture:** A Qt-free `summary_export` module serializes a summary to markdown/plaintext/HTML (consolidating two duplicated builders). A small Qt `ui/pdf_export` module renders HTML→PDF via `QTextDocument`/`QPrinter` and dispatches export by file extension. The main content area becomes a `QStackedWidget` (history+summary page / master-todo page) driven by a new sidebar "Todos" item; a new `MasterTodoView` renders todos grouped by meeting with interactive checkboxes that reuse the existing `TodoStateRepo`.

**Tech Stack:** Python 3.11, PySide6 (Qt 6, incl. QtPrintSupport), pytest, SQLite. Run tests with uv. `uv` is NOT on PATH — invoke by full path: `& "C:\Users\brian\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest`. UI tests use the `qapp` fixture in `tests/ui/conftest.py`; DB fixtures use `build_database(paths.db_path); db.initialize()` with `AppPaths(root=tmp_path); paths.ensure_dirs()`.

Spec: `docs/superpowers/specs/2026-05-27-phase-10-pdf-export-master-todos-design.md`.

---

## File Structure

**Create:**
- `src/teams_transcriber/summary_export.py` — Qt-free serializers: `to_markdown`, `to_plaintext`, `to_html`, `_strip_html`.
- `src/teams_transcriber/ui/pdf_export.py` — Qt: `render_html_to_pdf`, `write_summary_export` (suffix dispatch).
- `src/teams_transcriber/ui/master_todo_view.py` — `MasterTodoView` widget.
- `tests/test_summary_export.py`, `tests/ui/test_pdf_export.py`, `tests/ui/test_master_todo_view.py`.

**Modify:**
- `src/teams_transcriber/ui/summary_pane.py` — `_copy_markdown` uses `summary_export.to_markdown`.
- `src/teams_transcriber/ui/sidebar.py` — "Todos" section + `todos_selected` signal + `select_bucket`.
- `src/teams_transcriber/ui/app.py` — `QStackedWidget`, master-view wiring, `_export_summary` PDF dispatch.
- `tests/ui/test_sidebar.py` (create if absent) — sidebar signal tests.

---

## Task 1: Qt-free summary serializer

**Files:**
- Create: `src/teams_transcriber/summary_export.py`
- Test: `tests/test_summary_export.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_summary_export.py
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
    assert "[x] Email Jennifer" in txt or "☑" in txt
    assert "#" not in txt.split("\n")[0]  # title line not a markdown header


def test_html_is_well_formed_and_escapes_summary():
    s = _summary()
    s.summary = "a < b & c"
    html = summary_export.to_html(s, _rec(), {0: False, 1: False})
    assert "<html" in html.lower() or "<body" in html.lower()
    assert "a &lt; b &amp; c" in html
    assert "Email Jennifer" in html


def test_strip_html_renders_notes_as_text():
    assert summary_export._strip_html("<p>hello <b>world</b></p>").strip() == "hello world"
```

- [ ] **Step 2: Run to verify fail**

Run: `& "<uv>" run pytest tests/test_summary_export.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `summary_export.py`**

```python
"""Qt-free serialization of a Summary to markdown / plaintext / HTML.

Used by the Export action (md/txt/pdf) and the summary-pane Copy button, so the
output stays consistent and there is a single source of truth. `todo_states`
maps todo_index -> done (from TodoStateRepo); todos render with completion.
"""

from __future__ import annotations

import html as _html
import re
from datetime import datetime

from teams_transcriber.storage.models import Recording, Summary

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    text = _TAG_RE.sub("", s)
    return _html.unescape(text)


def _fmt_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%b %d, %Y, %I:%M %p")
    except ValueError:
        return iso


def _meta_line(summary: Summary, recording: Recording) -> str:
    minutes = (recording.duration_ms or 0) / 60000
    return f"{_fmt_time(recording.started_at)} · {minutes:.0f} min · {summary.model_used}"


def _title(summary: Summary, recording: Recording) -> str:
    return recording.display_title or summary.title or "Meeting"


def to_markdown(summary: Summary, recording: Recording, todo_states: dict[int, bool]) -> str:
    lines = [f"# {_title(summary, recording)}", "", f"_{_meta_line(summary, recording)}_", ""]
    if summary.summary:
        lines += [summary.summary, ""]
    if summary.my_todos:
        lines.append("## My todos")
        for i, t in enumerate(summary.my_todos):
            box = "x" if todo_states.get(i) else " "
            lines.append(f"- [{box}] {t.task}" + (f" (due {t.due})" if t.due else ""))
        lines.append("")
    if summary.action_items_others:
        lines.append("## Action items for others")
        for a in summary.action_items_others:
            lines.append(f"- {a.who}: {a.task}" + (f" (due {a.due})" if a.due else ""))
        lines.append("")
    if summary.key_decisions:
        lines.append("## Key decisions")
        lines += [f"- {d}" for d in summary.key_decisions]
        lines.append("")
    if summary.follow_ups:
        lines.append("## Follow-ups")
        lines += [f"- {f}" for f in summary.follow_ups]
        lines.append("")
    if summary.topics:
        lines.append("## Topics")
        lines.append(", ".join(summary.topics))
        lines.append("")
    notes = _strip_html(recording.manual_notes)
    if notes:
        lines += ["## My notes", notes, ""]
    return "\n".join(lines).rstrip() + "\n"


def to_plaintext(summary: Summary, recording: Recording, todo_states: dict[int, bool]) -> str:
    lines = [_title(summary, recording), _meta_line(summary, recording), ""]
    if summary.summary:
        lines += [summary.summary, ""]
    if summary.my_todos:
        lines.append("My todos")
        for i, t in enumerate(summary.my_todos):
            box = "[x]" if todo_states.get(i) else "[ ]"
            lines.append(f"  {box} {t.task}" + (f" (due {t.due})" if t.due else ""))
        lines.append("")
    if summary.action_items_others:
        lines.append("Action items for others")
        for a in summary.action_items_others:
            lines.append(f"  - {a.who}: {a.task}" + (f" (due {a.due})" if a.due else ""))
        lines.append("")
    if summary.key_decisions:
        lines.append("Key decisions")
        lines += [f"  - {d}" for d in summary.key_decisions]
        lines.append("")
    if summary.follow_ups:
        lines.append("Follow-ups")
        lines += [f"  - {f}" for f in summary.follow_ups]
        lines.append("")
    if summary.topics:
        lines += ["Topics", "  " + ", ".join(summary.topics), ""]
    notes = _strip_html(recording.manual_notes)
    if notes:
        lines += ["My notes", notes, ""]
    return "\n".join(lines).rstrip() + "\n"


def to_html(summary: Summary, recording: Recording, todo_states: dict[int, bool]) -> str:
    e = _html.escape
    parts = [
        "<html><head><meta charset='utf-8'></head>",
        "<body style=\"font-family: 'Segoe UI', sans-serif; color:#111827;\">",
        f"<h1 style='color:#065F46;'>{e(_title(summary, recording))}</h1>",
        f"<p style='color:#6B7280;font-size:12px;'>{e(_meta_line(summary, recording))}</p>",
    ]
    if summary.summary:
        parts.append(f"<p>{e(summary.summary)}</p>")
    if summary.my_todos:
        parts.append("<h2 style='color:#065F46;'>My todos</h2><ul>")
        for i, t in enumerate(summary.my_todos):
            mark = "☑" if todo_states.get(i) else "☐"
            due = f" (due {e(t.due)})" if t.due else ""
            parts.append(f"<li>{mark} {e(t.task)}{due}</li>")
        parts.append("</ul>")
    if summary.action_items_others:
        parts.append("<h2 style='color:#065F46;'>Action items for others</h2><ul>")
        for a in summary.action_items_others:
            due = f" (due {e(a.due)})" if a.due else ""
            parts.append(f"<li>{e(a.who)}: {e(a.task)}{due}</li>")
        parts.append("</ul>")
    if summary.key_decisions:
        parts.append("<h2 style='color:#065F46;'>Key decisions</h2><ul>")
        parts += [f"<li>{e(d)}</li>" for d in summary.key_decisions]
        parts.append("</ul>")
    if summary.follow_ups:
        parts.append("<h2 style='color:#065F46;'>Follow-ups</h2><ul>")
        parts += [f"<li>{e(f)}</li>" for f in summary.follow_ups]
        parts.append("</ul>")
    if summary.topics:
        parts.append("<h2 style='color:#065F46;'>Topics</h2>")
        parts.append(f"<p>{e(', '.join(summary.topics))}</p>")
    if recording.manual_notes:
        # manual_notes is our own NotesEditor HTML — embed directly.
        parts.append("<h2 style='color:#065F46;'>My notes</h2>")
        parts.append(recording.manual_notes)
    parts.append("</body></html>")
    return "".join(parts)
```

- [ ] **Step 4: Run to verify pass**

Run: `& "<uv>" run pytest tests/test_summary_export.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/summary_export.py tests/test_summary_export.py
git commit -m "feat(export): Qt-free summary serializer (markdown/plaintext/html)"
```

---

## Task 2: SummaryPane Copy reuses the serializer

**Files:**
- Modify: `src/teams_transcriber/ui/summary_pane.py` (`_copy_markdown`, ~line 230-253)
- Test: `tests/ui/test_summary_pane.py`

- [ ] **Step 1: Write the failing test**

```python
def test_copy_uses_done_state(qapp, summary_db):
    # summary_db: a recording with a summary having >=1 todo, and a TodoState row
    # marking todo 0 done. Build it mirroring the existing fixtures in this file.
    from PySide6.QtGui import QGuiApplication
    pane = SummaryPane(summary_db.db)
    pane.show_recording(summary_db.recording_id)
    pane._copy_markdown(summary_db.summary, summary_db.recording)
    text = QGuiApplication.clipboard().text()
    assert "- [x] " in text  # the done todo shows checked
```

> If building `summary_db` with a done TodoState is awkward, instead assert
> that `_copy_markdown` produces the same string as
> `summary_export.to_markdown(summary, recording, states)` for a known states
> dict — whichever is cleaner with the existing fixtures. The behavioral point:
> Copy reflects done-state via the shared serializer.

- [ ] **Step 2: Run to verify fail**

Run: `& "<uv>" run pytest tests/ui/test_summary_pane.py -k copy -v`
Expected: FAIL (current `_copy_markdown` always emits `- [ ]`).

- [ ] **Step 3: Rewrite `_copy_markdown`**

```python
    def _copy_markdown(self, summary: Summary, recording: Any) -> None:
        from teams_transcriber import summary_export
        states = {
            s.todo_index: s.done
            for s in TodoStateRepo(self._db).list_for_recording(summary.recording_id)
        }
        md = summary_export.to_markdown(summary, recording, states)
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(md)
```

(Remove the old inline line-building. `TodoStateRepo` is already imported in
summary_pane.py.)

- [ ] **Step 4: Run to verify pass**

Run: `& "<uv>" run pytest tests/ui/test_summary_pane.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/summary_pane.py tests/ui/test_summary_pane.py
git commit -m "refactor(ui): summary Copy uses shared serializer with done-state"
```

---

## Task 3: PDF render + export dispatch helper

**Files:**
- Create: `src/teams_transcriber/ui/pdf_export.py`
- Test: `tests/ui/test_pdf_export.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/ui/test_pdf_export.py
from pathlib import Path
from teams_transcriber.ui import pdf_export
# reuse the _rec()/_summary() builders from tests/test_summary_export.py by
# redefining them locally (small dataclasses) — see that file.


def test_render_html_to_pdf_writes_pdf(tmp_path, qapp):
    out = tmp_path / "out.pdf"
    pdf_export.render_html_to_pdf("<html><body><h1>Hi</h1></body></html>", str(out))
    assert out.exists() and out.stat().st_size > 0
    assert out.read_bytes()[:5] == b"%PDF-"


def test_write_summary_export_routes_by_suffix(tmp_path, qapp):
    rec = _rec(); summ = _summary(); states = {0: True, 1: False}
    md = tmp_path / "a.md"; txt = tmp_path / "a.txt"; pdf = tmp_path / "a.pdf"
    pdf_export.write_summary_export(str(md), summ, rec, states)
    pdf_export.write_summary_export(str(txt), summ, rec, states)
    pdf_export.write_summary_export(str(pdf), summ, rec, states)
    assert md.read_text(encoding="utf-8").startswith("# ")
    assert "[x]" in txt.read_text(encoding="utf-8") or "☑" in txt.read_text(encoding="utf-8")
    assert pdf.read_bytes()[:5] == b"%PDF-"
```

(Define `_rec()`/`_summary()` in this test file the same way as in
`tests/test_summary_export.py`.)

- [ ] **Step 2: Run to verify fail**

Run: `& "<uv>" run pytest tests/ui/test_pdf_export.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `ui/pdf_export.py`**

```python
"""HTML→PDF rendering + export-by-extension dispatch (Qt side of export).

summary_export does the Qt-free serialization; this module adds PDF (via
QtPrintSupport, bundled with PySide6) and picks the format from the path
suffix.
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
```

- [ ] **Step 4: Run to verify pass**

Run: `& "<uv>" run pytest tests/ui/test_pdf_export.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/pdf_export.py tests/ui/test_pdf_export.py
git commit -m "feat(export): HTML→PDF rendering + suffix-based export dispatch"
```

---

## Task 4: Wire PDF into the Export action

**Files:**
- Modify: `src/teams_transcriber/ui/app.py` (`_export_summary`, ~line 327-353)
- Test: covered by Task 3 (`write_summary_export`); add a thin app-level check below.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_app_export.py
def test_export_default_name_uses_title_and_pdf(qapp):
    # Pure helper test — see Step 3: factor the default filename into a module
    # function so it's testable without a QFileDialog.
    from teams_transcriber.ui.app import _default_export_name
    assert _default_export_name("Potter Sync", "2026-05-20T15:00:00+00:00").endswith(".pdf")
    assert "potter-sync" in _default_export_name("Potter Sync", "2026-05-20T15:00:00+00:00")
```

- [ ] **Step 2: Run to verify fail**

Run: `& "<uv>" run pytest tests/ui/test_app_export.py -v`
Expected: FAIL (`_default_export_name` undefined).

- [ ] **Step 3: Implement**

Add a module-level helper in `app.py`:
```python
def _default_export_name(title: str, started_at: str) -> str:
    import re
    from datetime import datetime
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "meeting").lower()).strip("-") or "meeting"
    try:
        day = datetime.fromisoformat(started_at).astimezone().strftime("%Y-%m-%d")
    except ValueError:
        day = "export"
    return f"{slug}-{day}.pdf"
```

Rewrite `_export_summary`:
```python
    def _export_summary(self, recording_id: int) -> None:
        rec = RecordingRepo(self.db).get(recording_id)
        s = SummaryRepo(self.db).get(recording_id)
        if rec is None or s is None:
            return
        default_name = _default_export_name(rec.display_title or s.title or "meeting", rec.started_at)
        path, _ = QFileDialog.getSaveFileName(
            self.window, "Export summary", default_name,
            "PDF (*.pdf);;Markdown (*.md);;Plain text (*.txt)",
        )
        if not path:
            return
        from teams_transcriber.storage import TodoStateRepo
        from teams_transcriber.ui.pdf_export import write_summary_export
        states = {
            st.todo_index: st.done
            for st in TodoStateRepo(self.db).list_for_recording(recording_id)
        }
        write_summary_export(path, s, rec, states)
```

(Delete the old inline markdown-building body. `_fmt_export_time` may become
unused — remove it if so.)

- [ ] **Step 4: Run to verify pass + full suite**

Run: `& "<uv>" run pytest tests/ui/test_app_export.py -v`
Expected: PASS
Run: `& "<uv>" run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py tests/ui/test_app_export.py
git commit -m "feat(ui): Export menu offers PDF (default), Markdown, plain text"
```

---

## Task 5: Sidebar "Todos" section

**Files:**
- Modify: `src/teams_transcriber/ui/sidebar.py`
- Test: `tests/ui/test_sidebar.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# tests/ui/test_sidebar.py
from teams_transcriber.ui.sidebar import Sidebar, SidebarBucket


def test_todos_button_emits_todos_selected(qapp):
    sb = Sidebar()
    got = []
    sb.todos_selected.connect(lambda: got.append(True))
    sb.todos_button.click()
    assert got == [True]
    assert sb._active_is_todos is True


def test_bucket_click_clears_todos_and_emits_bucket(qapp):
    sb = Sidebar()
    sb.todos_button.click()
    seen = []
    sb.bucket_selected.connect(seen.append)
    sb._buttons[SidebarBucket.MANUAL].click()
    assert seen == [SidebarBucket.MANUAL]
    assert sb._active_is_todos is False
    assert sb.active_bucket == SidebarBucket.MANUAL


def test_select_bucket_programmatically(qapp):
    sb = Sidebar()
    sb.todos_button.click()
    seen = []
    sb.bucket_selected.connect(seen.append)
    sb.select_bucket(SidebarBucket.ALL)
    assert seen == [SidebarBucket.ALL]
    assert sb._active_is_todos is False
```

- [ ] **Step 2: Run to verify fail**

Run: `& "<uv>" run pytest tests/ui/test_sidebar.py -v`
Expected: FAIL (`todos_selected`/`todos_button`/`select_bucket`/`_active_is_todos` missing).

- [ ] **Step 3: Implement**

In `sidebar.py`, add `todos_selected = Signal()` next to `bucket_selected`.
After the bucket-button loop and before `layout.addStretch(1)`, add the Todos
section:
```python
        todos_header = QLabel("Todos")
        todos_header.setProperty("role", "muted")
        todos_header.setStyleSheet("font-weight: 600; padding: 16px 8px 12px 8px;")
        layout.addWidget(todos_header)

        self.todos_button = QPushButton("To-Do List")
        self.todos_button.setProperty("sidebar_item", True)
        self.todos_button.clicked.connect(self._select_todos)
        layout.addWidget(self.todos_button)
```
Add `self._active_is_todos: bool = False` in `__init__` (before `_refresh_active`).
Add methods:
```python
    def _select_todos(self) -> None:
        self._active_is_todos = True
        self._refresh_active()
        self.todos_selected.emit()

    def select_bucket(self, bucket: SidebarBucket) -> None:
        """Programmatically select a History bucket (used by 'Go to summary')."""
        self._select(bucket)
```
Update `_select` to clear the todos flag:
```python
    def _select(self, bucket: SidebarBucket) -> None:
        self._active = bucket
        self._active_is_todos = False
        self._refresh_active()
        self.bucket_selected.emit(bucket)
```
Update `_refresh_active` to also handle the todos button:
```python
    def _refresh_active(self) -> None:
        for bucket, btn in self._buttons.items():
            btn.setProperty("active", (not self._active_is_todos) and bucket == self._active)
            self._restyle(btn)
        if hasattr(self, "todos_button"):
            self.todos_button.setProperty("active", self._active_is_todos)
            self._restyle(self.todos_button)

    @staticmethod
    def _restyle(btn) -> None:
        style = btn.style()
        if style is not None:
            style.unpolish(btn)
            style.polish(btn)
```
(`_refresh_active` is called in `__init__` AFTER `self.todos_button` is created — verify ordering: create todos_button before the final `_refresh_active()` call. Move the `self._refresh_active()` call to the end of `__init__`.)

- [ ] **Step 4: Run to verify pass**

Run: `& "<uv>" run pytest tests/ui/test_sidebar.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/sidebar.py tests/ui/test_sidebar.py
git commit -m "feat(ui): sidebar Todos section with todos_selected + select_bucket"
```

---

## Task 6: MasterTodoView widget

**Files:**
- Create: `src/teams_transcriber/ui/master_todo_view.py`
- Test: `tests/ui/test_master_todo_view.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/ui/test_master_todo_view.py
import pytest
from teams_transcriber.config import load_settings
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    build_database, RecordingRepo, SummaryRepo, TodoStateRepo,
)
from teams_transcriber.storage.models import (
    Recording, RecordingSource, RecordingStatus, Summary, TodoItem,
)
from teams_transcriber.ui.master_todo_view import MasterTodoView


@pytest.fixture
def db(tmp_path, qapp):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    d = build_database(paths.db_path); d.initialize()
    yield d
    d.close()


def _add(db, title, todos):
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-20T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title=title, display_title=title,
        audio_path=None, audio_deleted_at=None, duration_ms=1000,
        status=RecordingStatus.DONE, error_message=None,
    ))
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title=title, one_line=None, summary="s",
        key_decisions=[], my_todos=[TodoItem(task=t) for t in todos],
        action_items_others=[], follow_ups=[], topics=[],
        generated_at="2026-05-20T11:00:00+00:00", model_used="m",
    ))
    return rec.id


def test_reload_groups_only_meetings_with_todos(db):
    rid_a = _add(db, "Has Todos", ["one", "two"])
    _add(db, "No Todos", [])
    view = MasterTodoView(db)
    view.reload()
    # one group for "Has Todos", zero for "No Todos"
    assert view.group_count() == 1
    assert rid_a in view.group_recording_ids()


def test_go_to_summary_emits(db):
    rid = _add(db, "Meeting", ["x"])
    view = MasterTodoView(db); view.reload()
    seen = []
    view.go_to_summary.connect(seen.append)
    view._emit_go_to_summary(rid)   # or click the button if exposed
    assert seen == [rid]


def test_toggle_persists_and_emits(db):
    rid = _add(db, "Meeting", ["x"])
    view = MasterTodoView(db); view.reload()
    seen = []
    view.todo_toggled.connect(seen.append)
    view._toggle(rid, 0, "x", True)   # internal handler
    states = {s.todo_index: s.done for s in TodoStateRepo(db).list_for_recording(rid)}
    assert states.get(0) is True
    assert seen == [rid]


def test_empty_state(db):
    view = MasterTodoView(db); view.reload()
    assert view.group_count() == 0
    assert view.is_empty() is True
```

- [ ] **Step 2: Run to verify fail**

Run: `& "<uv>" run pytest tests/ui/test_master_todo_view.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `ui/master_todo_view.py`**

```python
"""Master to-do list: every to-do across meetings, grouped by meeting.

Interactive checkboxes write through TodoStateRepo (same store as the summary
pane), so the Phase-9 history completion chip and the summary stay in sync.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from teams_transcriber.storage import (
    Database, RecordingRepo, SummaryRepo, TodoStateRepo,
)


def _fmt_day(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%b %d, %Y")
    except ValueError:
        return iso


class MasterTodoView(QScrollArea):
    go_to_summary = Signal(int)   # recording_id
    todo_toggled = Signal(int)    # recording_id

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = db
        self._group_ids: list[int] = []
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)
        self.setWidget(self._container)

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        vp = self.viewport()
        if vp is not None:
            self._container.setMaximumWidth(vp.width())

    def group_count(self) -> int:
        return len(self._group_ids)

    def group_recording_ids(self) -> list[int]:
        return list(self._group_ids)

    def is_empty(self) -> bool:
        return not self._group_ids

    def reload(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._group_ids = []

        rec_repo = RecordingRepo(self._db)
        sum_repo = SummaryRepo(self._db)
        todo_repo = TodoStateRepo(self._db)
        for rec in rec_repo.list_recent(limit=500):
            if rec.id is None:
                continue
            s = sum_repo.get(rec.id)
            if s is None or not s.my_todos:
                continue
            states = {st.todo_index: st.done for st in todo_repo.list_for_recording(rec.id)}
            self._layout.addWidget(self._build_group(rec, s, states))
            self._group_ids.append(rec.id)

        if not self._group_ids:
            empty = QLabel("No to-dos yet.")
            empty.setProperty("role", "muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._layout.addWidget(empty)
        self._layout.addStretch(1)

    def _build_group(self, rec, summary, states: dict[int, bool]) -> QWidget:
        card = QWidget()
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel(rec.display_title or summary.title or "Meeting")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(title, 1)
        day = QLabel(_fmt_day(rec.started_at))
        day.setProperty("role", "muted")
        header.addWidget(day)
        go = QPushButton("Go to summary")
        go.setProperty("role", "secondary")
        go.clicked.connect(lambda _checked=False, rid=rec.id: self._emit_go_to_summary(rid))
        header.addWidget(go)
        v.addLayout(header)

        for i, td in enumerate(summary.my_todos):
            cb = QCheckBox(td.task + (f"  (due {td.due})" if td.due else ""))
            cb.setChecked(bool(states.get(i)))
            cb.toggled.connect(
                lambda checked, rid=rec.id, idx=i, task=td.task: self._toggle(rid, idx, task, checked)
            )
            v.addWidget(cb)
        return card

    def _emit_go_to_summary(self, recording_id: int) -> None:
        self.go_to_summary.emit(recording_id)

    def _toggle(self, recording_id: int, idx: int, task: str, checked: bool) -> None:
        TodoStateRepo(self._db).upsert(recording_id, idx, task, checked)
        self.todo_toggled.emit(recording_id)
```

- [ ] **Step 4: Run to verify pass**

Run: `& "<uv>" run pytest tests/ui/test_master_todo_view.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/master_todo_view.py tests/ui/test_master_todo_view.py
git commit -m "feat(ui): MasterTodoView — todos grouped by meeting, interactive"
```

---

## Task 7: App wiring — stacked content + master view

**Files:**
- Modify: `src/teams_transcriber/ui/app.py` (`_build_main_content`, `_on_bucket`, add handlers)
- Test: `tests/ui/test_master_todo_view.py` or a small app-level check (see note)

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_sidebar.py — add a stack-switch integration-ish check via signals.
# Full App construction is impractical; instead assert the wiring contract at the
# widget level: a QStackedWidget switches index on todos_selected / bucket_selected.
def test_stacked_switch_contract(qapp):
    from PySide6.QtWidgets import QStackedWidget, QWidget
    from teams_transcriber.ui.sidebar import Sidebar, SidebarBucket
    stack = QStackedWidget(); page0 = QWidget(); page1 = QWidget()
    stack.addWidget(page0); stack.addWidget(page1)
    sb = Sidebar()
    sb.todos_selected.connect(lambda: stack.setCurrentIndex(1))
    sb.bucket_selected.connect(lambda _b: stack.setCurrentIndex(0))
    sb.todos_button.click()
    assert stack.currentIndex() == 1
    sb._buttons[SidebarBucket.ALL].click()
    assert stack.currentIndex() == 0
```

- [ ] **Step 2: Run to verify fail**

Run: `& "<uv>" run pytest tests/ui/test_sidebar.py::test_stacked_switch_contract -v`
Expected: PASS only after Sidebar from Task 5 exists; if Task 5 done, this passes immediately — in that case it's a guard test (acceptable). The real wiring lives in app.py (manually verified) per Step 3.

- [ ] **Step 3: Implement the app wiring**

In `_build_main_content` (`app.py`): import `QStackedWidget` and `MasterTodoView`. After building `body` (history+summary) and adding history/summary to `body_layout`, REPLACE the final `layout.addWidget(body, 1)` with a stack:
```python
        from PySide6.QtWidgets import QStackedWidget
        from teams_transcriber.ui.master_todo_view import MasterTodoView

        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(body)                 # index 0
        self.master_todos = MasterTodoView(self.db)
        self._content_stack.addWidget(self.master_todos)    # index 1
        self.master_todos.go_to_summary.connect(self._go_to_summary_from_todos)
        self.master_todos.todo_toggled.connect(
            lambda _rid: self._refresh_history(query=self.search.input.text() or None)
        )
        layout.addWidget(self._content_stack, 1)
```
Wire the sidebar (where `bucket_selected` is connected, ~line 233): add the
todos connection and make bucket selection switch to page 0:
```python
        self.window.sidebar.bucket_selected.connect(self._on_bucket)
        self.window.sidebar.todos_selected.connect(self._show_master_todos)
```
Also have `SummaryPane.todo_state_changed` reload the master view so the two
stay consistent — extend the existing connection:
```python
        self.summary.todo_state_changed.connect(self._on_todo_state_changed)
```
and add:
```python
    def _on_todo_state_changed(self, _rid: int) -> None:
        self._refresh_history(query=self.search.input.text() or None)
        self.master_todos.reload()
```
(Replace the existing inline `todo_state_changed` lambda from Phase 9 with this
method so master view stays in sync.)

Update `_on_bucket` to switch to page 0:
```python
    def _on_bucket(self, _bucket: SidebarBucket) -> None:
        self._content_stack.setCurrentIndex(0)
        self._refresh_history(query=self.search.input.text() or None)
```
(Preserve whatever `_on_bucket` did before — read it; it currently calls
`_refresh_history`. Keep that, just add the page switch.)

Add the new handlers:
```python
    def _show_master_todos(self) -> None:
        self.master_todos.reload()
        self._content_stack.setCurrentIndex(1)

    def _go_to_summary_from_todos(self, recording_id: int) -> None:
        # Return to History (ALL so the card exists), select + show the meeting.
        self.window.sidebar.select_bucket(SidebarBucket.ALL)  # emits bucket_selected → page 0 + refresh
        self._content_stack.setCurrentIndex(0)
        self._show_window()
        self.history.select(recording_id)
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `& "<uv>" run pytest tests/ui/test_sidebar.py -v`
Expected: PASS
Run: `& "<uv>" run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/app.py tests/ui/test_sidebar.py
git commit -m "feat(ui): stacked content + sidebar Todos view wiring"
```

---

## Task 8: Packaging — verify QtPrintSupport bundles

**Files:**
- Modify (only if needed): `installer/*.spec` / `scripts/build_installer.py`

- [ ] **Step 1: Check the smoke build imports QtPrintSupport**

The build script runs a smoke test of the frozen exe. Confirm
`from PySide6.QtPrintSupport import QPrinter` succeeds in the bundle. Quick
local check without a full build:
Run: `& "<uv>" run python -c "from PySide6.QtPrintSupport import QPrinter; print('ok')"`
Expected: `ok` (confirms the module exists in the env).

- [ ] **Step 2: If a later packaged build fails to import it**, add to the
PyInstaller spec `hiddenimports`: `'PySide6.QtPrintSupport'`, and ensure the
Qt `printsupport` platform plugin is collected (PyInstaller's PySide6 hook
usually handles this). Document the change in the spec file's comments.

- [ ] **Step 3: Commit (only if a spec change was needed)**

```bash
git add installer/ scripts/build_installer.py
git commit -m "build(installer): ensure QtPrintSupport bundles for PDF export"
```

(If no change is needed, note that in the task and skip the commit.)

---

## Final verification

- [ ] Run the full suite: `& "<uv>" run pytest -q` — all pass (≥ 314 baseline + new tests).
- [ ] Manual: export a summary as PDF (open it), as .md, as .txt; click sidebar
  "To-Do List" (grouping, due dates, completion); tick a todo there → history
  chip recolors + the meeting's summary shows it checked; "Go to summary"
  returns to History with that meeting selected.
- [ ] Update `memory/project_teams_transcriber.md` (Phase 10 summary) and mark
  the spec Status done; then `superpowers:finishing-a-development-branch`.

---

## Self-review notes (author)

- Spec coverage: PDF export → Tasks 1-4, 8; master to-do list → Tasks 5-7;
  interactive sync → Task 6 (`_toggle` upsert) + Task 7 (`_on_todo_state_changed`
  reload + history refresh). All spec sections mapped.
- Consolidation of duplicated markdown builders → Tasks 1-2, 4.
- Type consistency: `summary_export.to_markdown/to_plaintext/to_html(summary,
  recording, todo_states)` and `pdf_export.write_summary_export(path, summary,
  recording, todo_states)` and `render_html_to_pdf(html, out_path)` used
  consistently. `MasterTodoView.go_to_summary`/`todo_toggled` signals + `reload`
  /`group_count`/`group_recording_ids`/`is_empty` used consistently in Task 6
  & 7. `Sidebar.todos_selected`/`todos_button`/`select_bucket`/`_active_is_todos`
  consistent across Tasks 5 & 7.
- Note: `SummaryRepo.upsert(Summary)` signature assumed in tests — the
  implementer must verify the real `SummaryRepo` write method name/shape from
  `storage/summaries.py` and adapt the test fixtures accordingly.
