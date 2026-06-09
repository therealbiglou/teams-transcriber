# Phase 13 — Multi-Destination Wrike Sync

**Status:** Draft, 2026-06-09
**Predecessor phase:** Phase 11 (Wrike integration baseline, v0.7.x)

## Problem

Phase 11 shipped a single-folder, single-sync Wrike integration: one click,
all `my_todos` and `action_items_others` land in one chosen folder, with
assignees determined by case-insensitive exact full-name match.

Real meetings cover multiple topics that map to different Wrike projects.
Real action items name people by first-name only or by role
("the eng lead", "Mike from finance"), which the exact-match resolver misses.
And there's other meeting content — the summary itself, key decisions, follow-ups
— that the user wants to post into Wrike as well, in formats other than tasks
(comments, mainly).

## Goals

1. Let the user **split a meeting's data across multiple Wrike folders** in one
   send operation.
2. Let the user **send more than just todos** — summary, key decisions, follow-ups
   — and pick a **format per item** (task or comment).
3. **Intelligently suggest assignees** for action-items-for-others by combining
   fuzzy matching with an LLM fallback for ambiguous cases.
4. Make re-syncs safe: items already in Wrike from a prior sync are visibly
   locked so the user can't accidentally double-create.

## Non-goals (deferred)

- Writing to Wrike folder/project **descriptions** (append-vs-overwrite is a
  footgun; tasks + comments cover the common cases).
- Editing already-synced tasks (PATCHing title/description/assignee back from
  the planner). A re-sync of a synced row is a no-op in v1.
- Cross-recording aggregation (e.g., "all follow-ups from this week" as a
  single comment). Per-meeting only.
- Attachments / file uploads.

## Decisions (locked during brainstorming, 2026-06-09)

| # | Decision | Rationale |
|---|---|---|
| 1 | **Full planner UI** — one row per data item, per-row destination + format + assignee | Most flexible; the cost of complexity is paid once in the dialog, not by limiting the user. |
| 2 | **Auto-sync toast opens the planner** (replaces single-folder picker) | One code path; defaults populate the planner so it's still one-click for the common case. |
| 3 | **Fuzzy match + LLM fallback** for assignees | Free for the easy cases; smart when needed. Bounded LLM cost (one batched call per sync). |
| 4 | **LLM fallback on by default**, Settings toggle to disable | Best out-of-box; opt-out for users who want zero extra API spend. |
| 5 | **Locked-when-synced** re-sync behaviour | Prevent accidental double-creates; manual edits live in Wrike. |
| 6 | **v1 formats: task and comment only** | Description writes are deferred (overwrite vs. append decision is ambiguous). |

## Architecture

### Unified item model

Every meeting-derived item the planner can ship to Wrike becomes a `SyncItem`:

```python
# src/teams_transcriber/integrations/wrike_sync.py
from dataclasses import dataclass
from typing import Literal

SyncKind = Literal["summary", "decisions", "my_todo", "action_other", "follow_up"]

@dataclass(slots=True)
class SyncItem:
    kind: SyncKind
    index: int                    # 0 for singletons; per-list-element index otherwise
    text: str                     # the content (task title, comment body, …)
    suggested_who: str | None     # raw "who" string from the LLM; action_other only
```

`recording_to_sync_items(db, recording_id) -> list[SyncItem]` is the single
conversion point. Order is stable so row indices line up between renders and
between sync attempts.

### The planner widget

`src/teams_transcriber/ui/wrike_sync_planner.py::WrikeSyncPlanner`

- Themed frameless modal, same chrome as `WrikeFolderPicker`.
- One row per `SyncItem`. Each row:
  - **Checkbox** (left): include/exclude this row from the send batch.
  - **Kind badge + preview**: kind icon + first 80 chars of `text` (selectable label, word-wrap).
  - **Format dropdown**: kind-gated options, default per `RECOMMENDED_FORMAT`.
  - **Destination button**: label = current folder name; click opens the existing
    `WrikeFolderPicker` modal; selection updates the button label.
  - **Assignee combo** (only for `kind == "action_other"`, rendered below the row): searchable contact list,
    pre-populated with the fuzzy/LLM suggestion; includes "Unassigned" as the first option.
- **Already-synced rows**:
  - Show a `✓ synced` badge to the right of the kind preview.
  - Checkbox is **checked but disabled**; format/destination dropdowns are also disabled.
  - The row still renders so the user sees what's already in Wrike.
- **Footer**: `[Cancel]  [Send N items →]`. `N` is the count of checked, non-locked rows; updates live as checkboxes toggle.
- **Empty / no-API-key state**: planner refuses to open if Wrike isn't configured; the "Send to Wrike" button is gated on `_wrike_is_configured()` as today.
- **Width**: ~720 px (wider than the folder picker; needs room for the dropdowns).

#### Format options by kind

| Kind | Default | Allowed |
|---|---|---|
| `summary` | Comment | Comment, Task |
| `decisions` | Comment | Comment, Task |
| `my_todo` | Task | Task |
| `action_other` | Task | Task |
| `follow_up` | Task | Task, Comment |

For `decisions` as Task: the task title is `"Key decisions from <meeting>"` and the body is a bullet list of all decisions (i.e., one task, not one per decision). For `decisions` as Comment: one comment containing the same bulleted body.

For `follow_up` as Comment: each follow-up becomes its own comment on the destination folder.

### Auto-sync flow

| Event | Today | New |
|---|---|---|
| `SummaryReady` | Toast → `WrikeFolderPicker` → sync all my_todos + action_others to one folder | Toast `"Wrike: send N items?"` → `WrikeSyncPlanner` opens pre-populated with defaults |
| "Send to Wrike" button (past meetings) | Same as toast path | Opens `WrikeSyncPlanner` |
| Startup pending retry | Toast for oldest pending sync | Same toast, but action opens planner |

The `WrikeFolderPicker` modal **stays in the code** — it's used inside the
planner as the per-row destination picker. It is no longer reached as a
top-level dialog from anywhere.

### Defaults at open

When the planner opens:

- All non-synced rows: checked.
- Format: per `RECOMMENDED_FORMAT` table above.
- Destination: head of `wrike_recent_folder_ids` (the existing LRU). If the
  LRU is empty (first-ever sync), the destination button reads
  `"Pick a folder…"` and the row is unchecked + Send is disabled until
  every checked row has a destination.
- Assignee (action_other only): result of `suggest_assignees(items, contacts, summary)`.

### Assignee resolution

```python
# src/teams_transcriber/integrations/wrike_assignees.py

def suggest_assignees(
    items: list[SyncItem],
    contacts: list[Contact],
    *,
    meeting_summary: str | None,
    api_key: str | None,
    llm_fallback: bool,
    anthropic_client_factory: ClientFactory | None = None,
) -> dict[int, str | None]:
    """Return {sync_item.index: contact_id or None} for action_other items."""
```

Two-pass:

1. **Fuzzy pass.** Token-similarity score (token-sort-ratio) of `suggested_who`
   against every contact's `full_name`. If best score ≥ 0.85 and beats the
   runner-up by ≥ 0.10, that contact is the match. Otherwise unresolved.
2. **LLM fallback** (gated on `llm_fallback`). Collect all unresolved items.
   Single Claude call with tool-use schema:
   ```json
   {
     "name": "resolve_assignees",
     "input_schema": {
       "type": "object",
       "properties": {
         "matches": {
           "type": "array",
           "items": {
             "type": "object",
             "properties": {
               "item_index": {"type": "integer"},
               "contact_id": {"type": ["string", "null"]}
             },
             "required": ["item_index", "contact_id"]
           }
         }
       },
       "required": ["matches"]
     }
   }
   ```
   Prompt:
   ```
   You resolve action-item assignees for a meeting to Wrike team members.
   For each unresolved item, return the best-matching team-member id or null
   if no team member is a confident fit.

   Meeting summary (for context):
   {summary}

   Unresolved items:
   - index=3  who="the eng lead"  task="finalize the IAM cutover plan"
   - index=5  who="Mike from finance"  task="confirm Q3 budget"

   Team members:
   - 12345  Sarah Kim
   - 67890  Mike Stone
   - 24680  Jennifer Patel
   ```
   Tool-use guarantees structured output. Falls back to `null` for any items
   the model declines to match.

**Dependency note.** Prefer `rapidfuzz` (small, pure-wheel install). If it's
not already a transitive dep, the fuzzy pass becomes a hand-rolled ~30-line
implementation in `wrike_assignees.py` — token-sort-ratio is straightforward.

### Wrike client additions

```python
# src/teams_transcriber/integrations/wrike_client.py

def create_comment(
    self,
    *,
    entity_type: Literal["folder", "task"],
    entity_id: str,
    text: str,
) -> str:
    """POST /folders/{id}/comments or /tasks/{id}/comments. Returns comment id."""
```

Task creation (`create_task`) already accepts `responsibles` — used for assignees. No change needed there.

### Sync orchestration

```python
# src/teams_transcriber/integrations/wrike_sync.py

@dataclass
class PlanRow:
    item: SyncItem
    folder_id: str
    format: Literal["task", "comment"]
    assignee_id: str | None  # only meaningful for action_other

def sync_items(db, recording_id: int, plan: list[PlanRow], *, client: WrikeClient) -> SyncReport:
    """Idempotent over (recording_id, item.kind, item.index)."""
```

For each row:
- Skip if `wrike_tasks` already has a row for `(rid, item.kind, item.index)` → that's the "locked-when-synced" guard.
- If `format == "task"`: `client.create_task(folder_id, title=item.text, responsibles=[assignee_id])`. Persist `wrike_tasks` row with `kind=item.kind, todo_index=item.index, task_id=..., folder_id=..., format="task", assignee_id=...`.
- If `format == "comment"`: `client.create_comment(entity_type="folder", entity_id=folder_id, text=item.text)`. Persist same row shape with `format="comment"` and `task_id=` storing the comment id (re-use the column; document in code).

Close-loop (today: `complete_task` on toggle of a my-todo) still works only for
rows whose `format == "task"` and `kind == "my"`; the filter widens trivially.

### Schema v6 — table rebuild (CHECK constraint widening)

`wrike_tasks.kind` has `CHECK (kind IN ('my', 'other'))` (confirmed by reading
`storage/schema_v4.py`). SQLite can't ALTER a CHECK constraint, so schema_v6
follows the Phase 9 v3 precedent: rebuild the table with the widened CHECK +
the two new columns, copy rows over, drop the old table, rename the new one,
recreate the index. The `MigrationRunner` already toggles
`PRAGMA foreign_keys = OFF` around each migration (Phase 9 lesson), so the
DROP doesn't cascade-delete `wrike_tasks` rows referenced from elsewhere
(currently nothing references them; defensive nonetheless).

```python
# storage/schema_v6.py (sketch)
_STATEMENTS = (
    """
    CREATE TABLE wrike_tasks_new (
        id                INTEGER PRIMARY KEY,
        recording_id      INTEGER NOT NULL
                          REFERENCES recordings(id) ON DELETE CASCADE,
        kind              TEXT NOT NULL CHECK (kind IN
                              ('my', 'other', 'summary', 'decisions', 'follow_up')),
        todo_index        INTEGER NOT NULL,
        wrike_task_id     TEXT NOT NULL,
        wrike_folder_id   TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        last_synced_done  INTEGER NOT NULL DEFAULT 0,
        format            TEXT NOT NULL DEFAULT 'task'
                          CHECK (format IN ('task', 'comment')),
        assignee_id       TEXT,
        UNIQUE (recording_id, kind, todo_index)
    )
    """,
    """
    INSERT INTO wrike_tasks_new
        (id, recording_id, kind, todo_index, wrike_task_id, wrike_folder_id,
         created_at, last_synced_done, format, assignee_id)
    SELECT id, recording_id, kind, todo_index, wrike_task_id, wrike_folder_id,
           created_at, last_synced_done, 'task', NULL
    FROM wrike_tasks
    """,
    "DROP TABLE wrike_tasks",
    "ALTER TABLE wrike_tasks_new RENAME TO wrike_tasks",
    "CREATE INDEX wrike_tasks_recording_idx ON wrike_tasks (recording_id)",
)
```

Existing `{my, other}` rows are preserved with `format='task'` and
`assignee_id=NULL`. New kinds `{summary, decisions, follow_up}` are accepted
by the widened CHECK.

The `wrike_sync` table is unchanged. Its `folder_id` column is repurposed as
"last-used folder for this recording" — drives the LRU and is the default
destination when the planner opens. Each individual item's actual destination
lives on its `wrike_tasks` row.

### Settings

`Settings.integrations` (or wherever the Wrike toggle lives — verify path during
implementation) gains:

```python
wrike_llm_assignee_fallback: bool = True
```

Surfaced in Settings → Integrations as a single checkbox: "Use Claude to suggest
assignees for ambiguous names (one extra API call per sync)". When off, the
fuzzy pass is the only resolver.

## File layout

```
src/teams_transcriber/
  integrations/
    wrike_client.py          # + create_comment
    wrike_sync.py            # + recording_to_sync_items, + sync_items (PlanRow-based)
    wrike_assignees.py       # NEW: fuzzy + LLM resolver
  ui/
    wrike_sync_planner.py    # NEW: planner dialog
    wrike_folder_picker.py   # unchanged; used inline by the planner
  storage/
    schema_v6.py             # NEW: ALTER TABLE ADD COLUMN x 2
    wrike.py                 # + format/assignee_id round-trip on wrike_tasks
  settings.py                # + wrike_llm_assignee_fallback
```

Existing `wrike_sync.py::sync_recording(...)` is the old single-folder
orchestrator — keep it as a thin wrapper around `sync_items(...)` (callers
won't break) OR remove it and update its 1-2 callers. Plan time call.

## Data flow

```
SummaryReady event
    │
    ▼
toast "Wrike: send N items?"
    │ (click)
    ▼
recording_to_sync_items(db, rid)        → list[SyncItem]
    │
    ▼
WrikeClient.list_contacts()              → list[Contact]   (cached per planner-session)
    │
    ▼
suggest_assignees(items, contacts, …)    → dict[index → contact_id|None]
    │
    ▼
WrikeSyncPlanner.show()                  ← items + defaults + suggestions
    │ (user reviews + clicks Send)
    ▼
plan = planner.build_plan()              → list[PlanRow]
    │
    ▼
sync_items(db, rid, plan, client=…)      → SyncReport
    │
    ▼
toast "Wrike: sent K tasks, M comments" / per-failure toast
```

All API I/O remains on background threads with the 3-arg
`QTimer.singleShot(0, self.window, …)` hop back to the main thread (Phase 11
gotcha — never violate).

## Error handling

- **API auth failure**: toast "Wrike: check your token in Settings → Integrations" with an "Open Settings" action (deep-links to the tab). Same pattern as today.
- **Per-row failures**: `sync_items` accumulates per-row results; failures don't abort the batch. The post-sync toast summarizes (`"Wrike: sent 6 of 7 — 1 failed"`) and the failed rows show up as un-synced on next planner open.
- **LLM resolution failure**: log a WARNING, treat all unresolved items as "Unassigned" suggestions, planner still opens. Never blocks the user.
- **No internet on auto-toast**: existing pending-retry path covers this.

## Testing

Tests on top of the existing Phase 11 fixtures:

- `tests/integrations/test_wrike_assignees.py` — fuzzy match cases (exact, partial, ambiguous → unresolved); LLM fallback with a fake Anthropic client; threshold tuning; empty contact list.
- `tests/integrations/test_wrike_sync_items.py` — `recording_to_sync_items` produces stable orderings; `sync_items` is idempotent on re-call; comment format hits the right client method; partial-failure batches.
- `tests/storage/test_wrike_schema_v6.py` — v5 → v6 migration (build a v5 DB by hand, upgrade, assert columns + defaults); existing `my`/`other` rows preserved.
- `tests/ui/test_wrike_sync_planner.py` — rows render per kind; format dropdown is kind-gated; checkbox math drives footer count; per-row folder override survives Send; already-synced rows disabled with badge; "Pick a folder…" state disables Send.
- `tests/ui/test_wrike_toast_opens_planner.py` — `SummaryReady` toast click opens the planner (mock the dialog `exec`).

Existing Phase 11 tests update where they depend on the old single-folder path
— mostly the toast-driven flow tests.

## Migration / backward compatibility

- Old `wrike_tasks` rows continue to load with `format='task'` (the column default) and `assignee_id=NULL`. Close-loop on these rows works unchanged.
- A user upgrading mid-meeting (i.e., a recording where Phase 11 sent some todos to one folder) sees those rows as "✓ synced" in the new planner. New items added to the same recording go through the planner normally.
- The `WrikeFolderPicker` modal stays in the codebase; only its call sites change.

## Open considerations (for plan-time)

- `rapidfuzz` is NOT in the dependency tree (verified). Hand-rolled
  token-sort-ratio in `wrike_assignees.py` (~30 lines). No new dep.
- `kind` badges: text-only chip labels (no icons) to avoid an asset pipeline.
  Use the existing `role=chip` theme token for each `SyncKind`.
