# Phase 11 — Wrike Integration

**Date:** 2026-06-07
**Status:** Approved (Brian, 2026-06-07)
**Branch:** `feature/phase-11-wrike-integration` (off Phase 10)

## Goals

Send every meeting's todos — both **My todos** and **Action items for others** —
to the user's Wrike instance as tasks, automatically when a summary completes.
Keep "checked off" state synchronized one-way (app → Wrike) so the user can
work either in Teams Transcriber or in Wrike and have one stay in step with
the other.

## Decisions locked with Brian (2026-06-07)

- **Auth:** Permanent Access Token (user generates in Wrike → pastes into
  Settings). Stored in Windows Credential Manager via `keyring`. Token is
  never accepted via chat; only the Settings dialog writes it (same rule as
  the Anthropic key).
- **Sync trigger:** Automatic on `SummaryReady`. No permanent "Send to Wrike"
  button on summary cards.
- **Per-meeting folder choice:** reconciled with the no-button rule via a
  **toast-driven folder picker** — the auto-sync surfaces a toast with a
  "Pick Wrike folder" action that opens a themed modal picker. If the toast
  is dismissed, the sync joins a pending queue and reappears as a single
  toast on next app launch.
- **Action items for others:** included in the sync. Wrike contact lookup
  attempts a **case-insensitive exact match on the full assignee name**; on
  match the task is assigned in Wrike, otherwise it's unassigned. Title is
  prefixed `For <who>: <task>`.
- **Close-loop:** **one-way, app → Wrike**. Toggling a my-todo's checkbox in
  the app updates the matching Wrike task to `Completed`/`Active` via the API
  using the stored task-id mapping.

## Architecture

```
SummaryReady ──┐                                     ┌── Wrike REST API
               ▼                                     ▼
  app.py handler                            wrike_client.py
   (toast + picker)  ── orchestrator ──►   (list_folders, list_contacts,
                       wrike_sync.py        create_task, complete_task,
                       (per-recording)      test_connection)
                            │
                            ▼
                     storage (schema_v4):
                     wrike_sync, wrike_tasks
```

- `integrations/wrike_client.py` — pure HTTP layer. Stateless. Takes the
  token + per-call args, returns parsed responses or raises a typed error.
  Uses **httpx** (already transitive via `anthropic`); no new top-level dep.
- `integrations/wrike_sync.py` — orchestrator: for one recording_id, builds
  the task payloads from `Summary.my_todos` + `Summary.action_items_others`,
  drives the client, and persists mappings to `wrike_tasks`. Idempotent: it
  skips todos already mapped in `wrike_tasks`.
- `ui/wrike_folder_picker.py` — themed frameless modal (uses the Phase-9
  `FramelessWindowMixin` + `TitleBar`). Lists recent folders (from
  `settings.integrations.wrike_recent_folder_ids`) plus a full folder
  list/search.
- `storage/schema_v4.py` — adds the two tables below. Rebuild not needed —
  pure `CREATE TABLE` additions (no `recordings` CHECK changes), so the
  migration is simple.
- `ui/settings_dialog.py` — new "Integrations" tab between **AI** and
  **Shortcuts**. Token field (password mask), Test connection, Enable Wrike
  sync checkbox.
- `ui/app.py` — wires `SummaryReady → _on_summary_ready_wrike`,
  `SummaryPane.todo_state_changed → _on_my_todo_toggled_close_loop`, and a
  startup check `_check_pending_wrike_syncs`.

## Data flow — auto sync

```
SummaryReady(recording_id)
   │
   ▼
App._on_summary_ready_wrike:
   if settings.integrations.wrike_enabled and pending row absent for rid:
       WrikeSyncRepo.upsert(rid, status='pending')
       show_in_app_toast("Sync N todos to Wrike",
                         "Pick a folder to send them.",
                         action_label="Pick folder",
                         action_callback=lambda: open_picker(rid))
   │
   ▼ user clicks
open_picker(rid):
   dlg = WrikeFolderPicker(client=WrikeClient(token), recent_ids=settings.recent_folder_ids)
   if dlg.exec() == Accepted:
       folder_id = dlg.selected_folder_id
       threading.Thread(target=lambda: run_sync(rid, folder_id), daemon=True).start()

run_sync(rid, folder_id):
   try:
       wrike_sync.sync_recording(db, client, rid, folder_id)
       WrikeSyncRepo.update(rid, status='synced', folder_id=folder_id)
       update settings.recent_folder_ids (LRU, cap 5)
       toast("Synced N tasks to Wrike", action="Open folder ↗")
   except WrikeAuthError:
       toast("Wrike auth failed — check token in Settings")
   except WrikeApiError as exc:
       WrikeSyncRepo.update(rid, status='failed', error_message=str(exc))
       toast("Wrike sync failed", str(exc))
```

## Data flow — close-loop

```
SummaryPane.todo_state_changed(rid)  (Phase 9 signal; emits after upsert)
   │
   ▼
App._on_my_todo_toggled_close_loop(rid):
   read all TodoState rows for rid
   read all wrike_tasks rows for (rid, kind='my')
   for each pair (idx, done) compared with last_synced_done:
       if changed:
           threading.Thread(target=client.complete_task,
                            args=(token, wrike_task_id, done), daemon=True).start()
           update wrike_tasks.last_synced_done = done
```

(Skipped if `wrike_enabled` is false or the wrike_tasks row is absent.)

## Data flow — pending retry on startup

```
App.__init__ tail:
   pending = WrikeSyncRepo.list_pending_or_failed()
   if pending:
       show_in_app_toast(f"{len(pending)} meetings pending Wrike sync",
                         "Pick folder to send.",
                         action_label="Pick folder",
                         action_callback=lambda: open_picker_for_oldest(pending))
```

Picks them off one-by-one (one toast → one folder pick → one sync); the next
pending is offered after the current one finishes.

## Settings

- New tab **Integrations** (between AI and Shortcuts).
- Fields:
  - `[Password]` Wrike API token (writes to keyring on Save).
  - `[Button]` Test connection — calls `GET /contacts/me`. Result label
    shows ✓ Connected as `<name>` / ✗ error.
  - `[Checkbox]` Enable Wrike sync (default off; gated on token presence).
- New settings.json keys:
  - `integrations.wrike_enabled: bool` (default false).
  - `integrations.wrike_recent_folder_ids: list[str]` (LRU, cap 5, default empty).
- Keyring: service `teams-transcriber`, user `wrike_api_token`.

## Schema v4

Pure additions — no recordings/source/status CHECK changes, so no rebuild
required (table-rebuild dance from v3 not needed):

```sql
CREATE TABLE wrike_sync (
    recording_id    INTEGER PRIMARY KEY
                    REFERENCES recordings(id) ON DELETE CASCADE,
    folder_id       TEXT,
    status          TEXT NOT NULL CHECK (status IN
                        ('pending', 'synced', 'failed', 'skipped')),
    last_attempted_at TEXT,
    error_message   TEXT
);

CREATE TABLE wrike_tasks (
    id               INTEGER PRIMARY KEY,
    recording_id     INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL CHECK (kind IN ('my', 'other')),
    todo_index       INTEGER NOT NULL,
    wrike_task_id    TEXT NOT NULL,
    wrike_folder_id  TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_synced_done INTEGER NOT NULL DEFAULT 0,    -- for close-loop dedup
    UNIQUE (recording_id, kind, todo_index)
);

CREATE INDEX wrike_tasks_recording_idx ON wrike_tasks (recording_id);
```

Both tables `ON DELETE CASCADE` from recordings so deleting a recording
removes its Wrike mappings (the Wrike tasks themselves are left in place —
the user owns them in Wrike).

## Wrike API specifics

- Base URL: `https://www.wrike.com/api/v4`
- Auth header: `Authorization: bearer <token>`
- Endpoints used:
  - `GET /contacts/me` — connection test, identifies "self" for assigning my-todos.
  - `GET /folders` — folder list for the picker.
  - `GET /contacts` — contact list for name-matching `action_items_others`.
  - `POST /folders/{folderId}/tasks` — create task. Body:
    `{"title": "...", "description": "...", "dates": {"due": "YYYY-MM-DD"}, "responsibles": [contactId], "status": "Active"}`
  - `PUT /tasks/{id}` — complete/uncomplete: `{"status": "Completed"}` / `"Active"`.
- Rate limit: respect HTTP 429 with exponential backoff (2 retries; then fail
  the sync, surface as toast).
- Errors:
  - 401/403 → `WrikeAuthError` (toast "Check token in Settings").
  - 429 → backoff + retry, else `WrikeRateLimitError`.
  - Other → `WrikeApiError` with the response detail.

## Task payload mapping

| Source field | Wrike task field |
|---|---|
| `TodoItem.task` (my todos) | `title` |
| `ActionItemOther.task` | `title`, prefixed `For <who>: ` |
| `TodoItem.context` (if any) | appended to `description` |
| `TodoItem.due` / `ActionItemOther.due` | `dates.due` (ISO date) |
| Meeting `display_title` + `started_at` | `description` (header line) |
| my todo → self contactId | `responsibles=[self_id]` |
| other → matched contactId or none | `responsibles=[...]` or omitted |
| Always | `status="Active"` on create |

## Testing

### Unit (no network)
- `WrikeClient` against a `pytest_httpx`-style `respx`/`httpx.MockTransport`:
  list_folders, list_contacts, create_task, complete_task, test_connection,
  401/429 paths.
- `wrike_sync.sync_recording` with a mocked client + in-memory db: my-todos
  + others, name-match hit + miss, idempotency (second call is a no-op for
  already-mapped todos).
- `schema_v4` migration data-safety test (insert pre-v4 rows, migrate, query
  the new tables, confirm rows survive).

### Widget
- `WrikeFolderPicker` smoke: builds with stubbed folder list, selects → emits
  folder_id, recent_ids displayed first.
- `SettingsDialog` Integrations tab smoke + a `Test connection` test with a
  mocked client.

### Manual
- Generate a Wrike token; paste in Settings; Test connection.
- Run a meeting; on summary ready, click the toast action; pick a folder;
  verify tasks appear in Wrike with correct titles / due dates / assignees.
- Toggle a my-todo in the summary pane; verify the Wrike task flips
  Completed/Active.
- Force a failure (revoke token mid-sync); verify the failed-sync toast and
  the retry path on next launch.

## Non-goals (deferred)

- Reverse sync (Wrike → app).
- Per-recording "remember the last folder I picked" / default folder.
- Updating Wrike tasks if a meeting's summary is regenerated (the create is
  a one-shot — re-running summary won't push edits).
- Manual permanent "Send to Wrike" button on summary cards (per Brian's "no
  button" choice).
- Manual contact picker UI when name match fails.
- Bulk per-folder operations / reassigning tasks between folders.

## Risks

| Risk | Mitigation |
|---|---|
| Token in logs / chat | Never logged; never accepted in chat; keyring only. Test-connection result label shows only the user's name, not the token. |
| Wrong-person assignment | Exact, case-insensitive full-name match only. Ambiguous (multiple matches) → unassigned. |
| Wrike API outage / 5xx | Surface failed sync via toast; retry next launch via the pending-syncs path. |
| Rate limit | Backoff with 2 retries on 429; then mark failed. |
| User regenerates summary → todos change indices | Existing wrike_tasks rows remain; new todos at new indices get new mappings; orphan mappings to deleted todos stay (no-op on close-loop). Acceptable for v1. |
| `recordings` cascade vs Wrike tasks | DB cascade removes mappings; Wrike tasks survive (the user owns them in Wrike — by design). |
