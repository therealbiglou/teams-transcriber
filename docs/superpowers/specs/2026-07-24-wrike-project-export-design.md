# Wrike Project Export — Design

**Date:** 2026-07-24
**Status:** Approved by Brian (brainstorm 2026-07-24)

## Purpose

Make Wrike the destination for every meeting. When a summary is ready, the
desktop app automatically creates a Wrike **project** for that meeting under
a configured Space/folder: the summary (plus key decisions) becomes the
project description, the transcript is attached as a Markdown file, and the
to-dos / follow-ups / action-items-for-others become tasks under the project.
Manual notes are posted as a comment. This replaces the existing item-by-item
"Send to Wrike" planner.

**Transition scope (decided):** Wrike is the destination; the app UI stays as
a local mirror + capture tool. Nothing in the app's read UI (summary pane,
transcript, master to-dos) is removed. Only the *outbound Wrike mechanism*
changes.

## Decisions (from brainstorming)

- **Trigger:** automatic on `SummaryReady`, when project-export is enabled and
  a destination is configured. Failures toast + enqueue for retry.
- **Relationship to the existing planner:** **replaces** it. The item-by-item
  planner dialog, folder picker, and per-item destination/format flow are
  removed. `WrikeClient`, assignee matching, and the pending-retry mechanism
  are reused.
- **Destination:** a Space, or a specific folder within a Space, chosen once
  in Settings. Meeting-projects are created directly under that parent.
- **Content mapping:**
  - Project **name** = meeting display title.
  - Project **description** (Markdown) = summary, followed by a "Key
    decisions" section.
  - **Transcript** = rendered Markdown, attached as a file.
  - **Tasks** under the project = my-todos + follow-ups + action-items-for-
    others (the last assigned to the matched Wrike contact via the existing
    name-match + optional Claude resolution).
  - Manual **notes** = a comment on the project.
  - Not synced: chat history (local Q&A tool).
- **Updates:** **create once, then hands-off.** No done-state close-loop, no
  pulling task status back. A re-summarize / manual re-send re-pushes:
  updates the description, replaces the transcript attachment, and creates any
  *new* tasks not already created — never duplicates.
- **Transcript attachment format:** Markdown (`.md`).

## Architecture

Extend the existing `integrations/wrike_*` layer; do not build a parallel
subsystem.

### Data flow (one push cycle, off the GUI thread)

Triggered by `SummaryReady` (or the manual "Send to Wrike" button):

1. **Find-or-create project.** Look up `wrike_projects[recording_id]`. Miss →
   `create_project(parent_id, title, description)` (Wrike folder with the
   `project` field set), store the returned id. Hit → reuse it and
   `update_project(project_id, description)`.
2. **Description** = `summary_export`-style Markdown: the summary body, then a
   `## Key decisions` section listing `summary.key_decisions`.
3. **Transcript** = rendered to Markdown (channel-tagged lines, reusing the
   transcript rendering the app already has) and attached via
   `upload_attachment`. On re-push, the prior attachment id (stored) is
   deleted/replaced.
4. **Tasks.** For each my-todo, follow-up, and action-item-for-other not
   already in the `wrike_tasks` mapping for this recording, `create_task`
   under the project. Action-items-for-others carry the resolved assignee.
   Record each new task in `wrike_tasks` (reused table) so re-push is
   idempotent.
5. **Notes.** If `recording.manual_notes` is non-empty, post it as a comment
   on the project (once — recorded so re-push doesn't duplicate).
6. **Report + toast.** Success → "Synced to Wrike" with an "Open in Wrike"
   action (project permalink). Failure → toast + enqueue in the existing
   `wrike_sync` pending/failed mechanism; retried on next launch.

### Components

| Unit | Responsibility |
|---|---|
| `integrations/wrike_client.py` (extend) | Add `list_spaces()` (GET /spaces), `create_project(parent_id, title, description) -> project`, `update_project(project_id, description)` (PUT /folders/{id}), `upload_attachment(entity_id, filename, content: bytes) -> attachment_id` (multipart POST /folders/{id}/attachments), `delete_attachment(attachment_id)`. Typed, tested against httpx mock transport. |
| `integrations/wrike_project_export.py` (new) | Pure orchestrator: `export_recording(db, client, recording_id, *, parent_id) -> ExportReport`. The find-or-create → describe → attach → tasks → comment cycle. No Qt, no threads. |
| `integrations/wrike_project_body.py` (new) | Pure builders: `build_description(summary) -> str` (summary + `## Key decisions`) and `build_transcript_md(segments) -> str`. Separate module so both are unit-tested without a client. |
| `storage/schema_v9.py` + `storage/wrike.py` | New `wrike_projects` table: `recording_id` (unique FK), `project_id`, `attachment_id`, `notes_comment_id`, `created_at`, `last_pushed_at`. `WrikeProjectRepo`. The existing `wrike_tasks` table records created task ids (reused); its done-state columns go unused for this model. |
| `ui/settings_dialog.py` (change) | Integrations tab Wrike section: keep token + Test connection. Replace the auto-send-todos + assignee-fallback checkboxes with: a project-export **enable** toggle, a **destination picker** button (opens a Space→folder chooser), and a read-only chosen-destination label. Persist `integrations.wrike_project_export_enabled`, `integrations.wrike_parent_id`, `integrations.wrike_parent_label`. Keep `wrike_llm_assignee_fallback` (still used for action-item assignees). |
| `ui/wrike_destination_picker.py` (new) | Themed frameless dialog: lists Spaces (`list_spaces`), drill into one to pick a folder (`list_folders` filtered to the space) or select the space root. Returns `(parent_id, label)`. Replaces `wrike_folder_picker.py`. |
| `ui/app.py` (change) | `_on_summary_ready_wrike` calls the project-export worker (background thread → `export_recording`, hop back for the toast) instead of the planner. Reuse the assignee-resolution preload and the pending-retry wiring. The SummaryPane "Send to Wrike" button triggers the same export (manual re-send). |

### Removed

`ui/wrike_sync_planner.py`, `ui/wrike_folder_picker.py`,
`integrations/wrike_sync.py` (item-by-item `sync_items`),
`integrations/wrike_items.py` (SyncItem model), and their tests. The
SummaryPane's planner-launch path. The Settings "auto-send todos when summary
ready" + per-item assignee UI.

## Error handling

- Auth / rate-limit / API errors → failure toast + `wrike_sync` enqueue;
  retried next launch (existing mechanism).
- Enabled but no destination configured → toast "Choose a Wrike destination
  in Settings → Integrations", no retry churn.
- Partial failure (project created, a task or the attachment failed) → what
  succeeded is recorded (`wrike_projects` / `wrike_tasks`), so a re-push
  resumes and completes the rest without duplicating.
- All Wrike I/O on a worker thread; toasts hop to the main thread via the
  3-arg `QTimer.singleShot(0, self.window, ...)` pattern.

## Testing

- `wrike_project_export`: full pytest coverage via a fake client — create-once,
  re-push updates description + replaces attachment + adds only new tasks,
  assignee matching for action-items-others, partial-failure resume, notes
  comment posted once.
- New `WrikeClient` methods: httpx mock transport (spaces list, project
  create/update, attachment multipart + delete).
- Body builders: description (summary + decisions) and transcript Markdown,
  pure snapshot-style assertions.
- Settings: destination persistence + enable-toggle roundtrip; picker returns
  `(parent_id, label)`.
- Remove the planner/folder-picker/sync_items/items tests with their modules.
- **De-risk against real Wrike after build** (like the MTP spike): confirm
  `create_project` (folder-as-project shape) and `upload_attachment`
  (multipart) behave as documented, using Brian's real token against a scratch
  Space. One manual script.

## Out of scope

Done-state close-loop / reverse sync (removed by decision), chat-history sync,
multi-destination routing, retre-assigning tasks after creation, and any
Android-side Wrike interaction (the phone mirrors the desktop DB only).
