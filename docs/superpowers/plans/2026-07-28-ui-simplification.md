# UI Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip every in-app meeting-reading surface, leaving a single history list whose rows show a derived status chip plus a Wrike action, a capture-only notes window during recording, and Settings.

**Architecture:** Build the new pieces first (pure status model → dialog tweak → row widget → list), rewire `MainWindow`/`App` onto them, then delete the dead modules, then drop `todo_state`. Deleting last keeps the test suite green at every step. No pipeline, integration, or existing-migration changes.

**Tech Stack:** Python 3.11, PySide6 (Qt 6), SQLite via `PRAGMA user_version` migrations, pytest + pytest-qt.

## Global Constraints

- Spec: [`docs/superpowers/specs/2026-07-28-ui-simplification-design.md`](../specs/2026-07-28-ui-simplification-design.md). Read it before Task 1.
- Run everything with `.venv/Scripts/python.exe` — **NOT `uv`** (not on PATH in this environment). Full suite: `.venv/Scripts/python.exe -m pytest -q`. Baseline before Task 1: **574 passed**.
- **Never `QMessageBox`** — use `ui/confirm_dialog.py::ConfirmDialog`. **Never OS toasts** — use `ui/toast_banner.py::show_in_app_toast`.
- Modal dialogs go through `ui/scrim.py::exec_modal` (already handled inside `ConfirmDialog.ask`/`.info`).
- All top-level windows are frameless via `ui/frameless.py::FramelessWindowMixin`, passing the layout holding the OuterFrame as `shell_layout=`.
- Never block the GUI thread; hop back with the 3-arg `QTimer.singleShot(0, <qobject>, callable)`.
- Reuse `ui/theme.py` role properties (`primary`, `secondary`, `ghost`, `danger`) and `chip` style — no inline stylesheets for new widgets.
- User-visible text uses `ui/labels.py` helpers; set `Qt.TextFormat.PlainText` on labels showing LLM/user content.
- **Do not remove or alter any existing migration.** Only `schema_v10` is added.
- **Do not touch** detection, recording, transcription, summarization, or the Wrike export path.
- Exact chip strings, used verbatim: `Processing…` (with a real ellipsis character), `Failed`, `Recording failed`, `Not in Wrike`, `In Wrike`, `Wrike failed`.
- Exact action labels: `Retry`, `Send to Wrike`, `Open in Wrike`, `Delete`.
- Conventional commits (`feat(ui):`, `refactor(ui):`, `test:`…). One commit per task.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/teams_transcriber/ui/meeting_status.py` **(new)** | Pure `(RecordingStatus, Wrike state) → RowState` mapping. No Qt. |
| `src/teams_transcriber/ui/meeting_row.py` **(new)** | One history row: title, when, chip, action buttons. Emits signals. |
| `src/teams_transcriber/ui/notes_window.py` **(new)** | Small notes-capture window shown while recording. |
| `src/teams_transcriber/storage/schema_v10.py` **(new)** | Drops `todo_state`. |
| `ui/confirm_dialog.py` | Gains a `selectable` body option for error detail. |
| `ui/history_list.py` | Reworked: rows not cards, no todo counts, no selection. |
| `ui/main_window.py` | Sidebar and content stack removed; hosts the list. |
| `ui/app.py` | Rewired to the new list; dead handlers removed. |
| **Deleted** | `summary_pane.py`, `workspace_window.py`, `chat_card.py`, `chat.py`, `storage/chat.py`, `master_todo_view.py`, `sidebar.py`, `live_transcript_view.py`, `transcript_window.py`, `pdf_export.py`, `summary_export.py`, `meeting_card.py`, `storage/todos.py` |

---

### Task 1: Pure status model

**Files:**
- Create: `src/teams_transcriber/ui/meeting_status.py`
- Test: `tests/ui/test_meeting_status.py`

**Interfaces:**
- Consumes: `teams_transcriber.storage.models.RecordingStatus`.
- Produces: `RowAction` (StrEnum: `NONE`, `RETRY`, `SEND_TO_WRIKE`, `OPEN_IN_WRIKE`), frozen dataclass `RowState(chip: str, action: RowAction, action_label: str | None, error_message: str | None)`, and
  `derive_row_state(*, status, error_message=None, has_wrike_project=False, wrike_permalink=None, wrike_sync_status=None, wrike_error_message=None) -> RowState`.
  `RowState.error_message` is non-None exactly when the chip is clickable.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_meeting_status.py
from __future__ import annotations

import pytest

from teams_transcriber.storage.models import RecordingStatus
from teams_transcriber.ui.meeting_status import RowAction, derive_row_state


@pytest.mark.parametrize("status", [
    RecordingStatus.RECORDING,
    RecordingStatus.TRANSCRIBING,
    RecordingStatus.SUMMARIZING,
    RecordingStatus.WAITING_FOR_NOTES,
])
def test_in_progress_shows_processing_and_no_action(status):
    state = derive_row_state(status=status)
    assert state.chip == "Processing…"
    assert state.action is RowAction.NONE
    assert state.action_label is None
    assert state.error_message is None


@pytest.mark.parametrize("status", [
    RecordingStatus.TRANSCRIPTION_FAILED,
    RecordingStatus.SUMMARY_FAILED,
])
def test_pipeline_failure_is_retryable_and_clickable(status):
    state = derive_row_state(status=status, error_message="boom")
    assert state.chip == "Failed"
    assert state.action is RowAction.RETRY
    assert state.action_label == "Retry"
    assert state.error_message == "boom"


def test_recording_failure_is_clickable_but_not_retryable():
    state = derive_row_state(
        status=RecordingStatus.RECORDING_FAILED, error_message="no mic",
    )
    assert state.chip == "Recording failed"
    assert state.action is RowAction.NONE
    assert state.action_label is None
    assert state.error_message == "no mic"


def test_done_without_wrike_offers_send():
    state = derive_row_state(status=RecordingStatus.DONE)
    assert state.chip == "Not in Wrike"
    assert state.action is RowAction.SEND_TO_WRIKE
    assert state.action_label == "Send to Wrike"
    assert state.error_message is None


def test_done_with_permalink_offers_open():
    state = derive_row_state(
        status=RecordingStatus.DONE,
        has_wrike_project=True,
        wrike_permalink="https://www.wrike.com/open.htm?id=1",
        wrike_sync_status="synced",
    )
    assert state.chip == "In Wrike"
    assert state.action is RowAction.OPEN_IN_WRIKE
    assert state.action_label == "Open in Wrike"


def test_wrike_failure_is_clickable_and_retries_the_push():
    state = derive_row_state(
        status=RecordingStatus.DONE,
        has_wrike_project=True,
        wrike_permalink="https://w/1",
        wrike_sync_status="failed",
        wrike_error_message="401 unauthorized",
    )
    assert state.chip == "Wrike failed"
    assert state.action is RowAction.SEND_TO_WRIKE
    assert state.action_label == "Retry"
    assert state.error_message == "401 unauthorized"


def test_project_without_permalink_falls_back_to_send():
    """A re-push is idempotent and repairs the missing permalink."""
    state = derive_row_state(
        status=RecordingStatus.DONE, has_wrike_project=True, wrike_permalink=None,
    )
    assert state.chip == "Not in Wrike"
    assert state.action is RowAction.SEND_TO_WRIKE
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_meeting_status.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'teams_transcriber.ui.meeting_status'`

- [ ] **Step 3: Write the implementation**

```python
# src/teams_transcriber/ui/meeting_status.py
"""Derives what one history row shows: a status chip and its action.

Pure — no Qt, no database — so every state combination is unit-testable.
``RowState.error_message`` is non-None exactly when the chip is clickable
(clicking it opens the failure detail dialog).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from teams_transcriber.storage.models import RecordingStatus

_IN_PROGRESS = frozenset({
    RecordingStatus.RECORDING,
    RecordingStatus.TRANSCRIBING,
    RecordingStatus.SUMMARIZING,
    RecordingStatus.WAITING_FOR_NOTES,
})

_RETRYABLE_FAILURES = frozenset({
    RecordingStatus.TRANSCRIPTION_FAILED,
    RecordingStatus.SUMMARY_FAILED,
})


class RowAction(StrEnum):
    NONE = "none"
    RETRY = "retry"
    SEND_TO_WRIKE = "send_to_wrike"
    OPEN_IN_WRIKE = "open_in_wrike"


@dataclass(frozen=True, slots=True)
class RowState:
    chip: str
    action: RowAction
    action_label: str | None
    error_message: str | None


def derive_row_state(
    *,
    status: RecordingStatus,
    error_message: str | None = None,
    has_wrike_project: bool = False,
    wrike_permalink: str | None = None,
    wrike_sync_status: str | None = None,
    wrike_error_message: str | None = None,
) -> RowState:
    if status in _IN_PROGRESS:
        return RowState("Processing…", RowAction.NONE, None, None)

    if status in _RETRYABLE_FAILURES:
        return RowState("Failed", RowAction.RETRY, "Retry", error_message)

    if status is RecordingStatus.RECORDING_FAILED:
        # The audio was never captured, so there is nothing to re-run.
        return RowState("Recording failed", RowAction.NONE, None, error_message)

    # status is DONE from here on.
    if wrike_sync_status == "failed":
        return RowState(
            "Wrike failed", RowAction.SEND_TO_WRIKE, "Retry", wrike_error_message,
        )

    if has_wrike_project and wrike_permalink:
        return RowState("In Wrike", RowAction.OPEN_IN_WRIKE, "Open in Wrike", None)

    # No project, or a project row with no usable permalink — a re-push is
    # idempotent and repairs the record.
    return RowState("Not in Wrike", RowAction.SEND_TO_WRIKE, "Send to Wrike", None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_meeting_status.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/meeting_status.py tests/ui/test_meeting_status.py
git commit -m "feat(ui): pure status model for meeting history rows"
```

---

### Task 2: Selectable error text in ConfirmDialog

**Files:**
- Modify: `src/teams_transcriber/ui/confirm_dialog.py`
- Test: `tests/ui/test_confirm_dialog.py` (create if absent)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ConfirmDialog.__init__(..., selectable: bool = False)` and `ConfirmDialog.info(parent, *, title, body, ok_label="OK", selectable=False)`. When `selectable=True` the body label is mouse-selectable so a long error can be copied.

**Why:** the spec requires failure detail to be readable and copyable. Today `body_lbl` wraps but has no text-interaction flags.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_confirm_dialog.py
from __future__ import annotations

from PySide6.QtCore import Qt

from teams_transcriber.ui.confirm_dialog import ConfirmDialog


def _body_label(dlg):
    from PySide6.QtWidgets import QLabel
    # title is the first QLabel, body the second
    return dlg.findChildren(QLabel)[1]


def test_body_is_not_selectable_by_default(qtbot):
    dlg = ConfirmDialog(title="T", body="plain")
    qtbot.addWidget(dlg)
    flags = _body_label(dlg).textInteractionFlags()
    assert not (flags & Qt.TextInteractionFlag.TextSelectableByMouse)


def test_selectable_body_can_be_copied(qtbot):
    dlg = ConfirmDialog(title="T", body="a long api error", selectable=True)
    qtbot.addWidget(dlg)
    flags = _body_label(dlg).textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_confirm_dialog.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'selectable'`

- [ ] **Step 3: Implement**

In `confirm_dialog.py`, add `selectable: bool = False` to `__init__`'s keyword-only parameters (after `danger`), and immediately after the existing `body_lbl.setStyleSheet(...)` line add:

```python
        if selectable:
            body_lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            body_lbl.setCursor(Qt.CursorShape.IBeamCursor)
```

Then add `selectable: bool = False` to the `info` classmethod's keyword-only parameters and pass it through to the constructor:

```python
        dlg = cls(
            title=title, body=body,
            confirm_label=ok_label, cancel_label=None,
            selectable=selectable, parent=parent,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_confirm_dialog.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/confirm_dialog.py tests/ui/test_confirm_dialog.py
git commit -m "feat(ui): optional selectable body text in ConfirmDialog"
```

---

### Task 3: MeetingRow widget

**Files:**
- Create: `src/teams_transcriber/ui/meeting_row.py`
- Test: `tests/ui/test_meeting_row.py`

**Interfaces:**
- Consumes: `derive_row_state`, `RowAction`, `RowState` from Task 1; `Recording` and `RecordingStatus` from `storage.models`; `ConfirmDialog.info(..., selectable=True)` from Task 2.
- Produces: `class MeetingRow(QFrame)` constructed as
  `MeetingRow(recording: Recording, state: RowState, parent=None)`, exposing signals
  `action_requested = Signal(int, str)` (recording_id, `RowAction` value) and
  `delete_requested = Signal(int)`, plus `show_error_detail()` which opens the
  failure dialog. Row is inert apart from its buttons and (when failed) its chip.

**Notes for the implementer:** the row is a `QFrame` with the existing card look — set `setProperty("card", True)` if the theme defines it, otherwise leave unstyled and let `theme.py` handle it; do NOT add an inline stylesheet. Chip uses the theme's `chip` style property. Duration comes from `recording.duration_ms` (may be `None` → omit). Format the timestamp with `datetime.fromisoformat(...).astimezone().strftime("%b %d, %-I:%M %p")` — on Windows use `%#I` instead of `%-I`; write it as a small helper so it is testable.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_meeting_row.py
from __future__ import annotations

from teams_transcriber.storage.models import Recording, RecordingSource, RecordingStatus
from teams_transcriber.ui.meeting_row import MeetingRow, format_when
from teams_transcriber.ui.meeting_status import RowAction, derive_row_state


def _rec(rid=1, status=RecordingStatus.DONE, duration_ms=2_280_000):
    return Recording(
        id=rid, started_at="2026-07-26T15:31:00+00:00", ended_at=None,
        source=RecordingSource.TEAMS, detected_title="X", display_title="Q3 Sync",
        audio_path=None, audio_deleted_at=None, duration_ms=duration_ms,
        status=status, error_message=None,
    )


def test_format_when_includes_date_and_duration():
    text = format_when("2026-07-26T15:31:00+00:00", 2_280_000)
    assert "Jul 26" in text
    assert "38 min" in text


def test_format_when_omits_duration_when_unknown():
    assert "min" not in format_when("2026-07-26T15:31:00+00:00", None)


def test_row_shows_title_and_chip(qtbot):
    rec = _rec()
    row = MeetingRow(rec, derive_row_state(status=RecordingStatus.DONE))
    qtbot.addWidget(row)
    assert row.title_label.text() == "Q3 Sync"
    assert row.chip_label.text() == "Not in Wrike"


def test_action_button_emits_action_with_id(qtbot):
    rec = _rec(rid=7)
    row = MeetingRow(rec, derive_row_state(status=RecordingStatus.DONE))
    qtbot.addWidget(row)
    with qtbot.waitSignal(row.action_requested) as sig:
        row.action_button.click()
    assert sig.args == [7, RowAction.SEND_TO_WRIKE.value]


def test_no_action_button_when_action_is_none(qtbot):
    rec = _rec(status=RecordingStatus.RECORDING)
    row = MeetingRow(rec, derive_row_state(status=RecordingStatus.RECORDING))
    qtbot.addWidget(row)
    assert row.action_button is None


def test_delete_button_emits_delete_with_id(qtbot):
    rec = _rec(rid=9)
    row = MeetingRow(rec, derive_row_state(status=RecordingStatus.DONE))
    qtbot.addWidget(row)
    with qtbot.waitSignal(row.delete_requested) as sig:
        row.delete_button.click()
    assert sig.args == [9]


def test_failed_chip_opens_detail_dialog(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "teams_transcriber.ui.meeting_row.ConfirmDialog.info",
        lambda parent, **kw: calls.append(kw),
    )
    rec = _rec(status=RecordingStatus.SUMMARY_FAILED)
    state = derive_row_state(
        status=RecordingStatus.SUMMARY_FAILED, error_message="429 rate limited",
    )
    row = MeetingRow(rec, state)
    qtbot.addWidget(row)
    row.show_error_detail()
    assert len(calls) == 1
    assert "429 rate limited" in calls[0]["body"]
    assert calls[0]["selectable"] is True


def test_non_failed_chip_does_not_open_dialog(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "teams_transcriber.ui.meeting_row.ConfirmDialog.info",
        lambda parent, **kw: calls.append(kw),
    )
    row = MeetingRow(_rec(), derive_row_state(status=RecordingStatus.DONE))
    qtbot.addWidget(row)
    row.show_error_detail()
    assert calls == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_meeting_row.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'teams_transcriber.ui.meeting_row'`

- [ ] **Step 3: Implement**

```python
# src/teams_transcriber/ui/meeting_row.py
"""One row in the meeting history: title, when, status chip, actions.

The row is inert apart from its buttons and — when the meeting failed — its
chip, which opens a detail dialog carrying the full error.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from teams_transcriber.storage.models import Recording
from teams_transcriber.ui.confirm_dialog import ConfirmDialog
from teams_transcriber.ui.labels import ElidedLabel, make_selectable
from teams_transcriber.ui.meeting_status import RowAction, RowState

_MAX_ERROR_CHARS = 2000


def format_when(started_at: str, duration_ms: int | None) -> str:
    """'Jul 26, 3:31 PM · 38 min' — duration omitted when unknown."""
    try:
        dt = datetime.fromisoformat(started_at).astimezone()
        stamp = dt.strftime("%b %d, %I:%M %p").replace(" 0", " ")
    except ValueError:
        stamp = started_at
    if not duration_ms:
        return stamp
    return f"{stamp} · {round(duration_ms / 60000)} min"


class MeetingRow(QFrame):
    action_requested = Signal(int, str)   # recording_id, RowAction value
    delete_requested = Signal(int)        # recording_id

    def __init__(
        self, recording: Recording, state: RowState, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        assert recording.id is not None
        self._recording_id = recording.id
        self._state = state

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.title_label = ElidedLabel(recording.display_title or "Untitled meeting")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        text_col.addWidget(self.title_label)

        self.when_label = make_selectable(
            ElidedLabel(format_when(recording.started_at, recording.duration_ms))
        )
        self.when_label.setProperty("role", "muted")
        text_col.addWidget(self.when_label)
        outer.addLayout(text_col, 1)

        self.chip_label = ElidedLabel(state.chip)
        self.chip_label.setProperty("chip", True)
        if state.error_message:
            self.chip_label.setCursor(Qt.CursorShape.PointingHandCursor)
            self.chip_label.setToolTip("Click for details")
        outer.addWidget(self.chip_label)

        self.action_button: QPushButton | None = None
        if state.action is not RowAction.NONE and state.action_label:
            self.action_button = QPushButton(state.action_label)
            self.action_button.setProperty("role", "primary")
            self.action_button.clicked.connect(
                lambda: self.action_requested.emit(self._recording_id, state.action.value)
            )
            outer.addWidget(self.action_button)

        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("role", "ghost")
        self.delete_button.clicked.connect(
            lambda: self.delete_requested.emit(self._recording_id)
        )
        outer.addWidget(self.delete_button)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        if self.chip_label.geometry().contains(event.position().toPoint()):
            self.show_error_detail()
        super().mousePressEvent(event)

    def show_error_detail(self) -> None:
        """Open the failure dialog. No-op when the row isn't a failure."""
        if not self._state.error_message:
            return
        body = self._state.error_message[:_MAX_ERROR_CHARS]
        if len(self._state.error_message) > _MAX_ERROR_CHARS:
            body += "\n\n(truncated)"
        ConfirmDialog.info(
            self, title=self._state.chip, body=body, ok_label="Close", selectable=True,
        )
```

If `ElidedLabel` or `make_selectable` has a different signature than assumed, adapt to the real one in `ui/labels.py` — do not change `labels.py` itself.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_meeting_row.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/meeting_row.py tests/ui/test_meeting_row.py
git commit -m "feat(ui): meeting history row with status chip and actions"
```

---

### Task 4: Rework HistoryList onto MeetingRow

**Files:**
- Modify: `src/teams_transcriber/ui/history_list.py`
- Test: `tests/ui/test_history_list.py` (replace existing tests for the old API)

**Interfaces:**
- Consumes: `MeetingRow` (Task 3), `RowState`/`derive_row_state` (Task 1).
- Produces: `HistoryList.set_rows(rows: Iterable[tuple[Recording, RowState]]) -> None`, and re-emitted signals `action_requested = Signal(int, str)` and `delete_requested = Signal(int)`. Date-bucket headers (`Today`/`Yesterday`/`This week`/`Earlier`) are kept.

**Remove from this module:** `recording_selected`, `select()`, `_apply_selection`, `_on_card_clicked`, `_selected_id`, the `MeetingCard` import, `filter_for_bucket` (its only caller was the sidebar), and the `SidebarBucket` import. Keep `_bucket_label` and the `resizeEvent` viewport guard.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_history_list.py
from __future__ import annotations

from teams_transcriber.storage.models import Recording, RecordingSource, RecordingStatus
from teams_transcriber.ui.history_list import HistoryList
from teams_transcriber.ui.meeting_status import RowAction, derive_row_state


def _rec(rid, started_at, status=RecordingStatus.DONE):
    return Recording(
        id=rid, started_at=started_at, ended_at=None, source=RecordingSource.TEAMS,
        detected_title="x", display_title=f"Meeting {rid}", audio_path=None,
        audio_deleted_at=None, duration_ms=60_000, status=status, error_message=None,
    )


def test_set_rows_renders_one_row_per_recording(qtbot):
    lst = HistoryList()
    qtbot.addWidget(lst)
    rows = [
        (_rec(1, "2026-07-26T15:00:00+00:00"), derive_row_state(status=RecordingStatus.DONE)),
        (_rec(2, "2026-07-20T15:00:00+00:00"), derive_row_state(status=RecordingStatus.DONE)),
    ]
    lst.set_rows(rows)
    from teams_transcriber.ui.meeting_row import MeetingRow
    assert len(lst.findChildren(MeetingRow)) == 2


def test_set_rows_replaces_previous_content(qtbot):
    lst = HistoryList()
    qtbot.addWidget(lst)
    state = derive_row_state(status=RecordingStatus.DONE)
    lst.set_rows([(_rec(1, "2026-07-26T15:00:00+00:00"), state)])
    lst.set_rows([(_rec(2, "2026-07-26T15:00:00+00:00"), state)])
    from teams_transcriber.ui.meeting_row import MeetingRow
    assert len(lst.findChildren(MeetingRow)) == 1


def test_row_action_is_reemitted_by_the_list(qtbot):
    lst = HistoryList()
    qtbot.addWidget(lst)
    lst.set_rows([(_rec(5, "2026-07-26T15:00:00+00:00"),
                   derive_row_state(status=RecordingStatus.DONE))])
    from teams_transcriber.ui.meeting_row import MeetingRow
    row = lst.findChildren(MeetingRow)[0]
    with qtbot.waitSignal(lst.action_requested) as sig:
        row.action_button.click()
    assert sig.args == [5, RowAction.SEND_TO_WRIKE.value]


def test_row_delete_is_reemitted_by_the_list(qtbot):
    lst = HistoryList()
    qtbot.addWidget(lst)
    lst.set_rows([(_rec(6, "2026-07-26T15:00:00+00:00"),
                   derive_row_state(status=RecordingStatus.DONE))])
    from teams_transcriber.ui.meeting_row import MeetingRow
    row = lst.findChildren(MeetingRow)[0]
    with qtbot.waitSignal(lst.delete_requested) as sig:
        row.delete_button.click()
    assert sig.args == [6]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_history_list.py -q`
Expected: FAIL — `AttributeError: 'HistoryList' object has no attribute 'set_rows'`

- [ ] **Step 3: Implement**

Replace `set_recordings` with `set_rows`, swap `MeetingCard` for `MeetingRow`, connect each row's two signals to the list's own, and delete the selection machinery listed above:

```python
    action_requested = Signal(int, str)   # recording_id, RowAction value
    delete_requested = Signal(int)        # recording_id

    def set_rows(self, rows: Iterable[tuple[Recording, RowState]]) -> None:
        """Replace the list. Each item is (Recording, RowState)."""
        self._clear()
        rows_list = list(rows)
        now = datetime.now().astimezone()
        groups: dict[str, list[tuple[Recording, RowState]]] = {}
        for rec, state in rows_list:
            groups.setdefault(_bucket_label(rec.started_at, now), []).append((rec, state))

        for label in ("Today", "Yesterday", "This week", "Earlier"):
            items = groups.get(label, [])
            if not items:
                continue
            header = QLabel(label)
            header.setProperty("role", "muted")
            header.setStyleSheet("font-weight: 600; padding-top: 4px;")
            self._layout.insertWidget(self._layout.count() - 1, header)
            for rec, state in items:
                row = MeetingRow(rec, state)
                row.action_requested.connect(self.action_requested)
                row.delete_requested.connect(self.delete_requested)
                self._layout.insertWidget(self._layout.count() - 1, row)
```

Simplify `_clear` to drop the `self._cards` bookkeeping.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_history_list.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/history_list.py tests/ui/test_history_list.py
git commit -m "refactor(ui): history list renders status rows instead of meeting cards"
```

---

### Task 5: Strip MainWindow and rewire App

**Files:**
- Modify: `src/teams_transcriber/ui/main_window.py`
- Modify: `src/teams_transcriber/ui/app.py`
- Test: `tests/ui/test_app_history.py` (new)

**Interfaces:**
- Consumes: `HistoryList.set_rows` / `action_requested` / `delete_requested` (Task 4), `derive_row_state` (Task 1).
- Produces: `App._refresh_history()` populating the list, and `App._on_row_action(recording_id, action)` dispatching to the existing handlers.

**MainWindow changes:** delete the `Sidebar` import, `self.sidebar`, the `body_splitter`, and the content stack; the window becomes title bar + `HistoryList` filling the body. Keep the frameless shell, window-state persistence, and add a **Settings** button to the title bar (`TitleBar` already supports controls; add a button beside them wired to a new `settings_requested` signal).

**App changes:**
- `_refresh_history()` builds rows: for each `RecordingRepo(self.db).list_recent()`, read `WrikeProjectRepo(self.db).get(rec.id)` and `WrikeSyncRepo(self.db).get(rec.id)`, call `derive_row_state(...)`, and pass `(rec, state)` to `self.history.set_rows(...)`.
- `_on_row_action(recording_id, action)`: `RowAction.RETRY` → existing `self._retry_recording(recording_id)`; `RowAction.SEND_TO_WRIKE` → existing `self._wrike_export_worker(recording_id)`; `RowAction.OPEN_IN_WRIKE` → `webbrowser.open(permalink)` read from `WrikeProjectRepo`.
- `_on_row_delete(recording_id)`: confirm with `ConfirmDialog.ask(self.window, title="Delete meeting?", body=..., confirm_label="Delete", danger=True)`; on decline return without changes; on confirm delete the audio file if present then `RecordingRepo.delete(recording_id)`, then `_refresh_history()`. Import `ConfirmDialog` at module scope in `app.py` so the tests can monkeypatch `teams_transcriber.ui.app.ConfirmDialog.ask`.
- Remove now-dead members: `self.summary`, `self.master_todos`, `self._content_stack`, `self.sidebar` wiring, `_open_workspace`, `_open_transcript_window`, chat/export/notes handlers, and `summary.*` signal connections. Repoint `self.tray.open_workspace_requested` and `self.active_banner.clicked` to the notes window handler added in Task 6 — until Task 6 lands, point them at a stub method `_open_notes_window` that does nothing, and complete it in Task 6.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_app_history.py
"""The history list is populated from the DB with derived row states."""
from __future__ import annotations

from teams_transcriber.storage.models import RecordingStatus
from teams_transcriber.ui.meeting_status import RowAction


def test_refresh_history_derives_state_per_recording(app_with_db, qtbot):
    """A done recording with no Wrike project offers Send to Wrike."""
    app = app_with_db
    app._refresh_history()
    from teams_transcriber.ui.meeting_row import MeetingRow
    rows = app.history.findChildren(MeetingRow)
    assert rows, "expected at least one row"
    assert rows[0].chip_label.text() == "Not in Wrike"


def test_row_action_send_dispatches_to_wrike_worker(app_with_db, monkeypatch):
    app = app_with_db
    called = []
    monkeypatch.setattr(app, "_wrike_export_worker", lambda rid: called.append(rid))
    app._on_row_action(1, RowAction.SEND_TO_WRIKE.value)
    assert called == [1]


def test_row_action_retry_dispatches_to_retry(app_with_db, monkeypatch):
    app = app_with_db
    called = []
    monkeypatch.setattr(app, "_retry_recording", lambda rid: called.append(rid))
    app._on_row_action(1, RowAction.RETRY.value)
    assert called == [1]


def test_delete_asks_first_and_keeps_row_when_declined(app_with_db, monkeypatch):
    app = app_with_db
    monkeypatch.setattr(
        "teams_transcriber.ui.app.ConfirmDialog.ask", lambda *a, **k: False,
    )
    app._on_row_delete(1)
    from teams_transcriber.storage.recordings import RecordingRepo
    assert RecordingRepo(app.db).get(1) is not None


def test_delete_removes_recording_and_audio_when_confirmed(
    app_with_db, monkeypatch, tmp_path,
):
    app = app_with_db
    audio = tmp_path / "rec.opus"
    audio.write_bytes(b"x")
    from teams_transcriber.storage.recordings import RecordingRepo
    RecordingRepo(app.db).set_audio_path(1, str(audio))
    monkeypatch.setattr(
        "teams_transcriber.ui.app.ConfirmDialog.ask", lambda *a, **k: True,
    )
    app._on_row_delete(1)
    assert RecordingRepo(app.db).get(1) is None
    assert not audio.exists()
```

Add an `app_with_db` fixture to `tests/ui/conftest.py` following the pattern already used by the existing app tests (`tests/ui/test_app_wrike_project.py` shows how `App` is constructed with a temp database); seed it with one `DONE` recording whose id is 1.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_app_history.py -q`
Expected: FAIL — `AttributeError: 'App' object has no attribute '_on_row_action'`

- [ ] **Step 3: Implement the MainWindow and App changes described above.**

- [ ] **Step 4: Delete the tests this rewiring invalidates**

Removing the sidebar, summary pane, master-todo view, workspace and chat wiring from `app.py` breaks every test that drives them. Delete those test files now — their modules follow in Task 7 — so the suite stays green at this task's review gate. Find them with:

```bash
grep -rln "SummaryPane\|Sidebar\|MasterTodoView\|WorkspaceWindow\|ChatCard\|TranscriptWindow\|MeetingCard\|filter_for_bucket\|summary_export\|pdf_export" tests
```

Delete each file the grep names **except** the new tests written in Tasks 1–5. Do not weaken a test to make it pass — if a hit is a genuine regression in code that survives, fix the code.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: **green**. The count drops (deleted suites); no failures.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(ui): single history view; rewire app to row actions"
```

---

### Task 6: Notes window replaces the workspace

**Files:**
- Create: `src/teams_transcriber/ui/notes_window.py`
- Modify: `src/teams_transcriber/ui/app.py` (complete `_open_notes_window`)
- Test: `tests/ui/test_notes_window.py`

**Interfaces:**
- Consumes: `ui/notes_editor.py` (reused as the text area), `FramelessWindowMixin`, `TitleBar`, `RecordingRepo.set_manual_notes`.
- Produces: `class NotesWindow(FramelessWindowMixin, QWidget)` built as `NotesWindow(db, recording_id: int, parent=None)`, with a `stop_requested = Signal()` and a `save()` that persists the text via `RecordingRepo.set_manual_notes`. Notes save on close and on stop.

**Behavior:** a title bar reading "Meeting notes", the notes text area, and a **Stop recording** button. No live transcript. It is the target of the tray's open-workspace action, the active-recording banner click, and the `open_workspace` hotkey.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_notes_window.py
from __future__ import annotations

from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.ui.notes_window import NotesWindow


def test_notes_are_persisted_on_save(tmp_db_with_recording, qtbot):
    db, rid = tmp_db_with_recording
    win = NotesWindow(db, rid)
    qtbot.addWidget(win)
    win.set_text("Whitney owns the packaging deadline")
    win.save()
    assert "Whitney owns" in (RecordingRepo(db).get(rid).manual_notes or "")


def test_stop_button_emits_stop_requested(tmp_db_with_recording, qtbot):
    db, rid = tmp_db_with_recording
    win = NotesWindow(db, rid)
    qtbot.addWidget(win)
    with qtbot.waitSignal(win.stop_requested):
        win.stop_button.click()


def test_stop_saves_notes_first(tmp_db_with_recording, qtbot):
    db, rid = tmp_db_with_recording
    win = NotesWindow(db, rid)
    qtbot.addWidget(win)
    win.set_text("late note")
    win.stop_button.click()
    assert "late note" in (RecordingRepo(db).get(rid).manual_notes or "")
```

Add a `tmp_db_with_recording` fixture to `tests/ui/conftest.py` returning `(db, recording_id)` for a fresh temp database holding one `RECORDING`-status row.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_notes_window.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'teams_transcriber.ui.notes_window'`

- [ ] **Step 3: Implement `NotesWindow`,** reusing `notes_editor.py` for the text area and following `workspace_window.py`'s frameless setup (`_init_frameless(..., shell_layout=...)`) and window-state persistence key. Then complete `App._open_notes_window(recording_id)` to construct, show, and keep a reference to it, wiring `stop_requested` to the existing stop-recording handler.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ui/test_notes_window.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/teams_transcriber/ui/notes_window.py src/teams_transcriber/ui/app.py tests/ui/test_notes_window.py tests/ui/conftest.py
git commit -m "feat(ui): capture-only notes window during recording"
```

---

### Task 7: Delete the dead reading surfaces

**Files:**
- Delete: `src/teams_transcriber/ui/summary_pane.py`, `workspace_window.py`, `chat_card.py`, `master_todo_view.py`, `sidebar.py`, `live_transcript_view.py`, `transcript_window.py`, `pdf_export.py`, `meeting_card.py`
- Delete: `src/teams_transcriber/chat.py`, `src/teams_transcriber/storage/chat.py`, `src/teams_transcriber/summary_export.py`
- Delete: every test file that exists solely to test the above
- Modify: `src/teams_transcriber/ui/app.py`, `storage/__init__.py` (drop the chat re-export)

**Do NOT delete** `storage/schema_v5.py` (the `chat_messages` migration) — the migration chain must stay intact and the table is deliberately retained.

- [ ] **Step 1: Find every reference**

Run:
```bash
grep -rn "summary_pane\|SummaryPane\|workspace_window\|WorkspaceWindow\|chat_card\|ChatCard\|master_todo\|MasterTodoView\|sidebar\|Sidebar\|live_transcript\|transcript_window\|TranscriptWindow\|pdf_export\|summary_export\|meeting_card\|MeetingCard\|teams_transcriber.chat\|ChatRepo" src tests --include=*.py
```
Every hit is either deleted with its module or removed from `app.py`.

- [ ] **Step 2: Delete the modules and their tests, then strip the residual imports and handlers from `app.py`.**

- [ ] **Step 3: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: **green**. Any remaining failure names a reference that was missed — fix it rather than deleting the test that caught it.

- [ ] **Step 4: Lint**

Run: `.venv/Scripts/python.exe -m ruff check src tests`
Expected: no new unused-import or undefined-name findings from the removal (the repo carries pre-existing lint debt elsewhere — leave it alone).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(ui): remove summary pane, chat, master todos, transcript and export views"
```

---

### Task 8: Remove todo_state

**Files:**
- Create: `src/teams_transcriber/storage/schema_v10.py`
- Modify: `src/teams_transcriber/storage/migrations.py` (register v10), `storage/__init__.py`, `summarizer.py`
- Delete: `src/teams_transcriber/storage/todos.py` and its tests
- Test: `tests/storage/test_schema_v10.py`

**Interfaces:**
- Consumes: the `Migration` dataclass from `storage/migrations.py`.
- Produces: `SCHEMA_V10 = Migration(version=10, name="drop todo_state", apply=_apply)`.

**Order matters:** Task 7 must be complete first — `master_todo_view`, `summary_pane`, `pdf_export`, and `summary_export` all reference `TodoStateRepo` and are deleted there.

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_schema_v10.py
from __future__ import annotations

from teams_transcriber.storage import build_database


def test_todo_state_table_is_dropped(tmp_path):
    db = build_database(tmp_path / "t.db")
    db.initialize()
    with db.connect() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "todo_state" not in names


def test_other_tables_survive(tmp_path):
    db = build_database(tmp_path / "t.db")
    db.initialize()
    with db.connect() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert {"recordings", "transcript_segments", "summaries",
            "wrike_projects", "chat_messages"} <= names
    assert version == 10
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_schema_v10.py -q`
Expected: FAIL — `todo_state` still present and `user_version` is 9.

- [ ] **Step 3: Implement**

```python
# src/teams_transcriber/storage/schema_v10.py
"""v10: drop todo_state — to-do done-state was only ever read by the removed
in-app views. Wrike tasks are the checkboxes now, so the data has no consumer."""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration

_STATEMENTS = (
    "DROP TABLE IF EXISTS todo_state",
)


def _apply(conn: sqlite3.Connection) -> None:
    for stmt in _STATEMENTS:
        conn.execute(stmt)


SCHEMA_V10 = Migration(version=10, name="drop todo_state", apply=_apply)
```

Register it in `migrations.py` exactly the way `SCHEMA_V9` is registered, delete `storage/todos.py`, drop its re-export from `storage/__init__.py`, and remove the `TodoStateRepo` import and every write to it from `summarizer.py` (the summary itself is unaffected).

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: green, with the v10 tests passing.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(storage): drop todo_state table and repo (schema v10)"
```

---

## Final verification (after Task 8)

- [ ] `.venv/Scripts/python.exe -m pytest -q` — full suite green.
- [ ] `.venv/Scripts/python.exe -m ruff check src tests` — no new findings.
- [ ] `.venv/Scripts/python.exe -c "import teams_transcriber.ui.app, teams_transcriber.cli"` — imports clean.
- [ ] **Launch check (proxy-scrubbed):** `env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy .venv/Scripts/python.exe -m teams_transcriber` — the window opens showing only the history list; Settings opens from the title bar; a row's Delete asks for confirmation; a failed row's chip opens the error dialog. Close it afterward.
- [ ] `git log --oneline` — one conventional commit per task.
