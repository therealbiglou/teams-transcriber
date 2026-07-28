# UI Simplification — Design

**Date:** 2026-07-28
**Status:** Approved by Brian (brainstorm 2026-07-28)

## Purpose

Wrike is now where meetings are read (see
[`2026-07-24-wrike-project-export-design.md`](2026-07-24-wrike-project-export-design.md)).
The desktop app should stop being a second place to read them. It becomes a
**capture-and-dispatch tool**: detect and record meetings, transcribe and
summarize them, hand them to Wrike, and show just enough history to confirm
that happened — and to push anything that hasn't gone yet.

Everything that exists only to *read* a meeting inside the app is removed.

## Decisions (from brainstorming)

- **Manual notes: capture kept, viewing removed.** Notes still steer the
  summary (the prompt treats them as authoritative context for naming owners)
  and still post as the Wrike project comment. They are never displayed or
  edited after the meeting ends.
- **While recording: a small notes window only.** The split workspace with
  live transcription is removed.
- **Meeting chat (Q&A) is cut** — UI and backend. It was the only remaining
  reason for a per-meeting detail view.
- **No per-meeting detail view at all.** The history list is the whole reading
  surface.
- **Rows keep a Delete action** (removes the recording and its audio).
- **Buttons and failure chips are the only interaction** — clicking a row
  itself does nothing; clicking a failure chip explains the failure.
- **`todo_state` is removed**, not left write-only: the repo, the summarizer's
  writes, and the table itself all go.

## Architecture

### Unchanged

Meeting detection, dual-channel recording, the transcription and
summarization pipeline, the whole Wrike export path, Settings, the first-run
wizard, tray, hotkeys, toasts, the active-recording banner, and update checks.
This is a UI-layer change; no pipeline or integration behavior changes.

### Main window — a single meeting list

One view. No sidebar, no content stack, no detail pane.

Each row shows the meeting title, its start date/time and duration, one status
chip, and its actions.

**Status is derived, not stored.** A pure function maps
`(RecordingStatus, wrike state)` to a chip and a primary action, so it is unit
testable without Qt:

| Recording status | Wrike state | Chip | Primary action |
|---|---|---|---|
| `recording`, `transcribing`, `summarizing`, `waiting_for_notes` | any | `Processing…` | none |
| `transcription_failed`, `summary_failed` | any | `Failed` * | Retry |
| `recording_failed` | any | `Recording failed` * | none — Delete only |
| `done` | no `wrike_projects` row | `Not in Wrike` | **Send to Wrike** |
| `done` | `wrike_projects` row exists, `wrike_sync.status` not `failed` | `In Wrike` | **Open in Wrike** |
| `done` | `wrike_sync.status == "failed"` | `Wrike failed` * | Retry |

\* **Failure chips are clickable.** Clicking one opens a themed detail dialog
showing the meeting title and the full stored `error_message` as selectable,
wrapping text, so a long API error can be read and copied rather than
squinted at in a tooltip. It is informational — a single dismiss button, no
action. Implemented as an info mode on `ui/confirm_dialog.py` (never
`QMessageBox`, per the project's UI rules). Non-failure chips are inert.

`Open in Wrike` uses the stored `wrike_projects.permalink`; if it is missing,
the row falls back to `Send to Wrike` (a re-push is idempotent and repairs the
record). Every row also has **Delete**, which asks for confirmation via
`ui/confirm_dialog.py::ConfirmDialog.ask` and removes the recording and its
audio file.

Settings opens from a button in the title bar.

This also closes a real gap: until now nothing in the UI told the user *where*
an exported meeting went, which caused a long "nothing is happening"
investigation on 2026-07-27.

### While recording — notes window

A small frameless window: a text area bound to `recordings.manual_notes` plus
a stop-recording button. It replaces `WorkspaceWindow`. The existing
`ui/notes_editor.py` is reused as the text area. The tray's
"open workspace" action, the active-recording banner click, and the
`open_workspace` hotkey all now open this window.

### Removed

| Module | Reason |
|---|---|
| `ui/summary_pane.py` | summary/todos/decisions/follow-ups/notes reading surface |
| `ui/workspace_window.py` | replaced by the notes window |
| `ui/chat_card.py`, `chat.py`, `storage/chat.py` | chat cut |
| `ui/master_todo_view.py` | to-do reading surface |
| `ui/sidebar.py` | single view needs no navigation |
| `ui/live_transcript_view.py` | live transcript cut |
| `ui/transcript_window.py` | transcript reading surface |
| `ui/pdf_export.py`, `summary_export.py` | export cut |
| `storage/todos.py` (`TodoStateRepo`) | to-do state removed entirely |

Their tests are removed with them, along with the now-dead wiring in
`ui/app.py` and `ui/main_window.py`, and the summarizer's `TodoStateRepo`
writes. `ui/history_list.py` and `ui/meeting_card.py` are reworked into the
new row rather than deleted.

### Database

**No existing migration is removed or altered.** The chain is already applied
in the user's database; deleting a migration would break upgrades.

One migration is **added** — `schema_v10`, which drops the `todo_state`
table. To-do done-state was only ever read by the removed UI; Wrike tasks are
the checkboxes now, so the data has no remaining consumer. This discards
existing done-state, which is accepted.

`chat_messages` is deliberately **kept**. Chat is unreachable once its UI and
backend are gone, but the stored conversations are user content and dropping
them was not asked for. Removing that table later is a one-line migration if
wanted.

## Error handling

Failures surface in the row's status chip with the stored `error_message` in
its tooltip. This is the only place a failed meeting is visible, so the chip
must distinguish a pipeline failure from a Wrike failure — retrying the wrong
stage is confusing and, for a summary failure, costs an API call.

Retry reuses the existing path (`SummaryPane.retry_requested` →
`App._retry_recording` → `Pipeline.retry_summary`), rewired to the new row.
It is offered only for `transcription_failed` and `summary_failed`.
`recording_failed` gets no retry — the audio was never captured, so there is
nothing to re-run; that row offers Delete only, matching today's behavior.

Deletion is confirmed before it happens and removes both the database row and
the audio file.

## Testing

- The status/action mapping is a pure function with table-driven tests over
  every `RecordingStatus` × Wrike-state combination, including the
  missing-permalink fallback and which chips are clickable.
- The list renders rows for a seeded database, and each action emits the right
  signal for the right recording id (`pytest-qt`, matching existing UI tests).
- Clicking a failure chip opens the detail dialog carrying that recording's
  `error_message`; clicking a non-failure chip does nothing.
- Delete asks for confirmation and removes the row and audio file.
- `schema_v10` drops `todo_state` and leaves every other table intact, and a
  database already at v9 upgrades cleanly (matching the existing migration
  tests).
- Deleted modules' tests are removed; the remaining suite must stay green.

## Accepted trade-offs

- Transcripts are no longer readable in the app (they are attached to every
  Wrike project as `transcript.md`).
- **Meeting search is removed** (decided 2026-07-28, after a review flagged it
  as an undocumented loss). The history list shows the most recent 200
  meetings, newest first, with no filter; finding an older meeting means
  scrolling. Accepted because the list exists to push and open meetings, not
  to browse them — Wrike is where meetings are searched. `ui/search_bar.py`
  and its test were deleted rather than left as dead code.
- To-dos are no longer checkable in the app; Wrike tasks are the checkboxes.
- No PDF export.
- Chat history is unreachable.
- If Wrike is misconfigured the app is nearly featureless — mitigated by the
  `Not in Wrike` chip, which guarantees nothing is silently stranded.

## Out of scope

Changes to detection, recording, transcription, summarization, or the Wrike
export itself; any database migration; the installer's post-install launch
option (queued separately in `.superpowers/sdd/progress.md`).
