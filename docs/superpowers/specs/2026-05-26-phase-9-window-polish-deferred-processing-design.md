# Phase 9 — Window Polish + Deferred Processing

**Date:** 2026-05-26
**Status:** Approved (Brian, 2026-05-26)
**Branch:** `feature/phase-9-window-polish-deferred-processing` (off Phase 8)

## Goals

Seven items from Brian's testing feedback, grouped into three themes:

1. **Consistent, full window chrome** — every top-level window (current and
   future) is movable, edge-resizable, and carries the same controls as the
   main window. Today only `MainWindow` has full chrome; `WorkspaceWindow`
   moves but can't resize; `TranscriptWindow` can't move or resize.
2. **Defer post-processing while notes are open** — when a meeting ends and a
   notes/workspace window is open for that recording, hold
   transcription + summarization until the user closes the window, and alert
   them that the app is waiting. (Notes are a summarizer input, so processing
   early would drop the user's notes from the summary.)
3. **Todo-completion signal + better transcript/summary UX** — color the todo
   count by completion, add a "complete" count, rewrite the transcript view
   for smooth scrolling + full selection + tighter spacing, and make all
   summary text selectable.

## Decisions locked with Brian (2026-05-26)

- **Transcript view → rewritten as a single selectable document** (not the
  current per-row `QListWidget`). Gets smooth pixel scrolling, tight padding,
  and drag-select/copy across the whole transcript.
- **Summary pane → keep separate cards**, every label individually
  selectable. No cross-card drag selection (Brian accepted this tradeoff so
  the interactive todo checkboxes can stay).
- **Deferred processing → strict, no override.** Processing begins only when
  the notes window is closed. No "Process now" button.

## Feature 1 — Shared window chrome

### Problem

`MainWindow` (a `QMainWindow`) implements frameless chrome inline:
`_RESIZE_MARGIN`, `_edge_at`, `mousePressEvent`/`mouseMoveEvent` (edge resize
via `startSystemResize`), `_cursor_for_edges`, `_toggle_max`,
`_apply_outer_style` (16 px radius windowed / 0 maximized). `TitleBar`
(`title_bar.py`) provides drag-to-move + min/max/close buttons.
`WorkspaceWindow` and `TranscriptWindow` each reimplement a *subset*
(Workspace: move only; Transcript: neither).

### Approach — a `FramelessWindowMixin` + a configurable `TitleBar`

The windows have three different Qt base classes (`QMainWindow`, `QWidget`,
`QDialog`), so a shared *base class* won't fit all. Use a **mixin** that
installs chrome behavior on any `QWidget`-derived window, plus the existing
`TitleBar` widget made configurable.

New `src/teams_transcriber/ui/frameless.py`:

```python
class FramelessWindowMixin:
    """Adds frameless move + edge-resize + rounded corners to a QWidget.

    Host class MUST: (a) call self._init_frameless(outer_frame) after building
    its outer QFrame, (b) route its TitleBar's move drags through that bar.
    Provides mouseMove/mousePress edge-resize, cursor feedback, maximize
    toggle with corner-radius swap, and the drop-shadow outer style.
    """
    _RESIZE_MARGIN = 6

    def _init_frameless(self, outer: QFrame, *, resizable: bool = True) -> None: ...
    def _edge_at(self, pos: QPoint) -> Qt.Edges: ...
    def mousePressEvent(self, e): ...   # startSystemResize on edge hit
    def mouseMoveEvent(self, e): ...     # cursor feedback
    def _cursor_for_edges(self, edges) -> Qt.CursorShape: ...
    def toggle_max(self) -> None: ...    # swap radius 16<->0, update titlebar icon
    def _apply_outer_style(self) -> None: ...
```

`TitleBar` gains a `controls` parameter so each window picks its button set,
plus optional extra widgets (Workspace keeps its 📌 always-on-top toggle and
its red/grey recording-status dot):

```python
TitleBar(
    title: str,
    *,
    controls: tuple[str, ...] = ("min", "max", "close"),  # any subset
    extra_left: list[QWidget] | None = None,   # e.g. status dot
    extra_right: list[QWidget] | None = None,  # e.g. always-on-top toggle
)
# emits: minimize_requested, maximize_requested, close_requested
```

`TitleBar` already moves the window on drag (`startSystemMove` via the
window handle); that stays.

### Per-window control sets

| Window | Move | Resize | Controls |
|---|---|---|---|
| MainWindow | ✓ (today) | ✓ (today) | min, max, close |
| WorkspaceWindow | ✓ | **✓ (new)** | min, max, close + 📌 always-on-top + status dot |
| TranscriptWindow | **✓ (new)** | **✓ (new)** | min, max, close |
| SettingsDialog | **✓ (new)** | **✓ (new)** | max, close |
| FirstRunWizard | **✓ (new)** | **✓ (new)** | close |
| UpdateDialog | **✓ (new)** | **✓ (new)** | close |
| ConfirmDialog | **✓ (new)** | — (auto-sized) | close |

Rationale for the split: modal yes/no dialogs (`ConfirmDialog`) auto-size to
content, so resize is meaningless and `min` would orphan a modal. Everything
the user reads or works in for a while (Workspace, Transcript, Settings) gets
the full set. This honors "same controls as the main app" where it makes
sense and degrades gracefully where it doesn't.

### Chrome standardization

`MainWindow` uses `WA_TranslucentBackground=True` + an outer `QFrame` with
`border-radius` and resize from the true window edges (no shadow). Workspace
and Transcript currently use `WA_TranslucentBackground=False` + a 20 px shadow
margin (which also prevents edge-resize at the window boundary). Settings /
Wizard / Update are currently **native Windows chrome** `QDialog`s.

We standardize every window on MainWindow's pattern (translucent bg + rounded
outer frame + mixin edge-resize), because that is literally "the same as the
main app" and makes edge-resize work uniformly. The bespoke drop-shadow margin
on Workspace/Transcript is removed in the process. `ConfirmDialog` already
satisfies the goal (frameless, themed, drag-to-move, auto-sized) and is left
as-is.

### Migration

1. Refactor `MainWindow` to consume `FramelessWindowMixin` (move its inline
   resize/cursor/maximize logic into the mixin verbatim — no behavior change;
   existing main-window behavior is the reference implementation).
2. Make `TitleBar` configurable; replace `WorkspaceWindow`'s bespoke
   `_WorkspaceTitleBar` with the shared `TitleBar` + extras.
3. `TranscriptWindow`: build an outer `QFrame`, add `TitleBar`, call
   `_init_frameless`.
4. Dialogs adopt `TitleBar` + `_init_frameless` (Settings/Wizard/Update keep
   their existing themed bodies; just swap the chrome).
5. Remember each window's last size? **Out of scope** — windows open at their
   current default sizes; resize is per-session.

## Feature 2 — Defer processing while notes window is open

### New recording status

Add to `RecordingStatus` (a `StrEnum`):

```python
WAITING_FOR_NOTES = "waiting_for_notes"
```

**Correction (discovered during implementation):** the `recordings.status`
column has a SQL `CHECK (status IN (...))` constraint, so a **schema migration
IS required** — `schema_v3` rebuilds the `recordings` table with the expanded
allowed-value set (SQLite can't ALTER a CHECK in place), preserving all columns
and rows. The `MigrationRunner` toggles `PRAGMA foreign_keys = OFF` around each
migration so the table rebuild doesn't cascade-delete child rows.

### Gate mechanism (pipeline stays UI-agnostic)

`Pipeline.__init__` gains an optional plain callable — **not** a Qt object,
honoring the "EventBus is plain pub/sub, pipeline has no UI awareness"
commitment:

```python
processing_gate: Callable[[int], bool] | None = None
# returns True  -> defer post-processing for this recording_id
# returns False -> process immediately (current behavior)
```

Flow change in `_on_recording_finalized` (`pipeline.py:149`):

```python
def _on_recording_finalized(self, evt):
    if self._processing_gate is not None and self._processing_gate(evt.recording_id):
        RecordingRepo(self._db).update_status(
            evt.recording_id, RecordingStatus.WAITING_FOR_NOTES)
        with self._defer_lock:
            self._deferred[evt.recording_id] = evt
        self._bus.publish(ProcessingDeferred(recording_id=evt.recording_id))
        return
    self._submit_post_processing(evt.recording_id)   # extracted from current body
```

New public method:

```python
def release_processing(self, recording_id: int) -> None:
    with self._defer_lock:
        evt = self._deferred.pop(recording_id, None)
    if evt is None:
        return
    RecordingRepo(self._db).update_status(recording_id, RecordingStatus.TRANSCRIBING)
    self._submit_post_processing(recording_id)
```

`_deferred: dict[int, RecordingFinalized]` guarded by `threading.Lock()` —
the gate predicate is invoked from the recorder worker thread, release from
the Qt main thread.

### App wiring (the gate's UI half) — one shared predicate, no new event

`App` already tracks `self._workspace_windows: dict[int, WorkspaceWindow]` and
already wires `WorkspaceWindow.closed → _on_workspace_closed`. We make the
gate predicate and the UI's display decision the **same function**, so there
is exactly one source of truth and no event-ordering race:

- `App` keeps a `set[int]` of recording ids with an open workspace, guarded by
  a `threading.Lock` (the predicate is read from the recorder/watcher thread;
  the dict is mutated on the Qt main thread). Updated in `_open_workspace`
  (add) and `_on_workspace_closed` (remove).
- `App._should_defer_processing(rid) -> bool` reads that lock-guarded set.
- Pass `processing_gate=self._should_defer_processing` into `Pipeline`.

No new event type is added. The waiting UI surfaces (toast, tray, banner,
workspace footer) all live in `App` and are driven directly by
`_on_recording_finalized`, which calls the same `_should_defer_processing(rid)`
to choose between the existing "Transcribing…" path and the new waiting path.
This is simpler than a `ProcessingDeferred` event (which would only re-trigger
App methods and introduce an ordering race against the bridged
`RecordingFinalized`).

When a `WorkspaceWindow` closes, `_on_workspace_closed` removes the id from the
set and calls `pipeline.release_processing(rid)` (a no-op if the recording
wasn't deferred). If the recording *was* waiting, it also shows a "Processing
started" toast + sets tray PROCESSING + banner.

### The alert (waiting UI)

When `_should_defer_processing(rid)` is true at finalize time, `App`:
- shows an in-app toast: **"Waiting for notes — Transcription will start when
  you close the notes window."**,
- leaves the tray at IDLE (not PROCESSING — nothing is processing yet),
- hides the active-recording banner,
- calls `WorkspaceWindow.show_waiting_for_processing()` so a persistent footer
  line ("⏳ Transcription will start when you close this window") is visible in
  the window the user is looking at.

No action button (strict, per decision).

### Recovery safety

`_recover_stuck_recordings` gains a `WAITING_FOR_NOTES` branch: on startup no
workspace window is open, so any recording left waiting (app closed while
deferred) is processed — set `TRANSCRIBING` and enqueue `_run_post_processing`.
This prevents a waiting recording from being lost. (Without this, a deferred
recording left at `TRANSCRIBING` would be wrongly marked
`TRANSCRIPTION_FAILED` by the existing "TRANSCRIBING + no segments" rule —
which is exactly why we need the distinct status.)

### History card

`meeting_card.py::_status_chip` maps `WAITING_FOR_NOTES → ("Waiting for notes",
"warn")` (amber chip), so the history list shows the held state honestly.

## Feature 3 — Todo completion chip

### Behavior

The history card's todo chip (`meeting_card.py:93-104`) changes from a fixed
green `"{n} todos"` to completion-aware:

- text: `"{n} todos | {m} complete"` (singular `"1 todo | …"` when n == 1)
- color: `m == 0` → **red** (`variant="error"`); `0 < m < n` → **amber**
  (`variant="warn"`); `m == n` → **green** (`variant="success"`).

(The `variant` values map to existing chip styles in `theme.py`.)

### Data + live update

`MeetingCard.__init__` gains `todos_done: int` alongside `todo_count`. The
list builder (`HistoryList` / wherever cards are constructed) computes
`todos_done` via `TodoStateRepo(db).list_for_recording(rec_id)` (count
`done == True`), the same source `SummaryPane` already uses.

Live update: `SummaryPane` emits a new `todo_state_changed = Signal(int)`
(recording_id) whenever a checkbox toggles (right after it upserts
`TodoState`). `App` connects it and refreshes that recording's card chip
(rebuild the single card, or re-run the history query for that row). This
keeps the chip in sync as the user ticks items in the summary pane.

## Feature 4 — Transcript view rewrite (`LiveTranscriptView`)

### From `QListWidget` to a single `QTextEdit` (read-only)

Replace the per-row `QListWidget` + `_SegmentRow` widgets with one read-only
`QTextEdit` holding the whole transcript as one document. This delivers, by
construction:

- **Smooth pixel scrolling** (QTextEdit scrolls per-pixel; no item-jumping).
- **Full select/copy across lines** (one document = one selection).
- **Tight spacing** — controlled via block format margins / CSS, not per-row
  widget margins. Target: ~2–3 px between segments vs. today's 6 px top/bottom
  + 8 px list gaps.

### Rendering

Each segment becomes a styled block:

- A small inline channel tag — "ME" (emerald) / "OTHERS" (slate) — rendered
  as a colored `<span>`, followed by a muted timestamp and the text.
- Append incrementally via `QTextCursor` (live mode) so we don't re-render the
  whole document on each new segment.

### Public API preserved

Keep the existing method surface so `WorkspaceWindow` and `TranscriptWindow`
don't change their call sites:

- `load_segments(segments)` — clear + render all (used by TranscriptWindow and
  on Workspace reload).
- `append_segment(segment)` / the live-update slot — append one block.
- Smart auto-scroll: before appending, check if the vertical scrollbar is at
  (within tolerance of) max; if so, scroll to bottom after appending via
  `verticalScrollBar().setValue(maximum)`. If the user has scrolled up, leave
  their position — same UX intent as today, now pixel-smooth.

`QTextEdit.setReadOnly(True)` + `setTextInteractionFlags(TextSelectableBy
Mouse | TextSelectableByKeyboard)` for selection without editing.

## Feature 5 — Selectable summary text

Every `QLabel` in `SummaryPane` already has a `_make_selectable()` helper;
audit the pane and ensure **all** text labels (title, metadata, one-line,
summary body, key decisions, action items, follow-ups, topics/chips where
text-bearing, notes) pass through it. No structural change — just complete
coverage. (Cross-card selection is intentionally not supported, per decision.)

## Non-goals (deferred)

- Persisting per-window size/position across sessions.
- Cross-card text selection in the summary pane.
- A "Process now" override on the deferred-processing alert.
- Min/max controls on `ConfirmDialog` (auto-sized modal).
- Re-flowing the transcript document layout for print/export.

## Testing

### Unit / widget tests
- `FramelessWindowMixin._edge_at` returns correct `Qt.Edges` for corner/edge/
  interior points.
- `TitleBar` builds only the requested `controls`; emits the right signals.
- `WorkspaceWindow` / `TranscriptWindow` / `SettingsDialog` instantiate with
  the shared chrome (smoke construction under `QApplication`).
- `WorkspaceWindow.closed` signal fires on `closeEvent` with the recording id.
- `Pipeline._on_recording_finalized` with a gate returning True → status
  `WAITING_FOR_NOTES`, no executor submit, `ProcessingDeferred` published.
- `Pipeline.release_processing` → status `TRANSCRIBING` + post-processing
  submitted; no-op for an unknown id.
- `_recover_stuck_recordings`: `WAITING_FOR_NOTES` row → `TRANSCRIBING` +
  enqueued.
- `MeetingCard` todo chip: (0,n) red text/variant; (k,n) amber; (n,n) green;
  text formatting incl. singular.
- `SummaryPane.todo_state_changed` emits recording_id on checkbox toggle.
- `LiveTranscriptView`: `load_segments` renders all; `append_segment` adds one
  block; selectable + read-only flags set; auto-scroll only when at bottom.
- `SummaryPane`: every text label has selectable interaction flags.

### Manual
- Resize/move every window from each edge + corner; min/max/close work;
  corners square when maximized, rounded when windowed.
- Real meeting end with the workspace open → toast + footer note → no
  processing until close → on close, summary generates *with* the notes.
- Tick todos in the summary; history chip recolors red→amber→green live.
- Scroll a long transcript smoothly; drag-select across many lines and copy.

## Risks

| Risk | Mitigation |
|---|---|
| Refactoring MainWindow chrome regresses move/resize | Move logic verbatim into the mixin; keep existing main-window manual checks in the verification pass; widget tests for `_edge_at`. |
| Gate predicate read from worker thread races window dict | Snapshot via a `Lock`-guarded `set[int]`; predicate reads the snapshot, not the live dict. |
| Deferred recording lost if app closes while waiting | Distinct `WAITING_FOR_NOTES` status + recovery branch processes it on next start. |
| QTextEdit live-append re-layout cost on long meetings | Append via cursor (incremental), never re-set the whole document; auto-scroll only adjusts the scrollbar. |
| Auto-detected (Teams) meetings shouldn't defer | They don't auto-open a workspace, so the gate returns False — unchanged behavior. Manual recordings (workspace auto-opens) are the deferral case. |
