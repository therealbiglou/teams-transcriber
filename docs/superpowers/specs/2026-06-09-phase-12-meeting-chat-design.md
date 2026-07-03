# Phase 12 — Meeting Chat

**Date:** 2026-06-09
**Status:** Approved (Brian, 2026-06-09)
**Branch:** `feature/phase-12-meeting-chat` (off Phase 11)

## Goal

A persistent per-meeting chat with Claude embedded in `SummaryPane`, so the
user can ask follow-up questions about a meeting — "what was Jennifer's
actual deadline?" / "what did we decide about the floor plan?" — and Claude
answers using the full transcript + summary + manual notes as context. Chat
history persists across sessions and is always visible inside that
meeting's summary view.

## Decisions locked with Brian (2026-06-09)

- **Where:** new card at the bottom of `SummaryPane` (after the existing
  button row, before the trailing stretch). Matches the rest of the pane's
  card-stack pattern.
- **Context Claude sees:** **summary + manual_notes + full transcript**,
  wrapped in an Anthropic prompt-cache block so follow-ups are cheap.
- **Response mode:** **batch** (single reply at a time, no streaming for v1).

## Architecture

```
                      ┌──────────────────────────┐
                      │  ui/chat_card.py         │
                      │  ChatCard (QWidget):     │
                      │   - message list         │
                      │   - input + Send         │
                      │   - signals              │
                      └────────────┬─────────────┘
                                   │
                                   ▼
   App handler   ◄── send_requested(rid, text)
        │
        ├── persist user turn      ──► storage/chat.py
        ├── background thread:
        │     chat.ask(db, rid, msg, api_key)  ──► integrations/anthropic ──► Claude
        │     persists assistant turn
        │     returns reply
        ▼
   QTimer.singleShot(0, self.window, on_main_thread_done)
        │
        ▼
   ChatCard.append_assistant_message(reply)  /  show error bubble
```

- `storage/schema_v5.py` — pure CREATE TABLE addition (no rebuild).
- `storage/chat.py` — `ChatMessage` dataclass + `ChatRepo`
  (`list_for_recording`, `append`, `clear`).
- `chat.py` — Qt-free orchestrator. Reads context + history, calls Claude,
  persists turns, returns the reply. Raises typed errors.
- `ui/chat_card.py` — `ChatCard(QWidget)` rendering the message list +
  input. Emits `send_requested(int, str)` and `clear_requested(int)`.
- `ui/summary_pane.py` — when there are transcript segments, instantiate a
  `ChatCard`, load existing history, wire signals.
- `ui/app.py` — handler that runs the chat call in a daemon thread and
  hops the result back on the main thread (same threading pattern as
  Wrike).

## Schema v5

```sql
CREATE TABLE chat_messages (
    id            INTEGER PRIMARY KEY,
    recording_id  INTEGER NOT NULL
                  REFERENCES recordings(id) ON DELETE CASCADE,
    role          TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX chat_messages_recording_idx
    ON chat_messages (recording_id, id);
```

`ON DELETE CASCADE` so deleting a recording wipes its chat. The composite
index keeps `list_for_recording(rid)` ordered by `id` (insertion order =
chat order) without a separate sort.

## `storage/chat.py`

```python
@dataclass(slots=True)
class ChatMessage:
    id: int | None
    recording_id: int
    role: str            # 'user' | 'assistant'
    content: str
    created_at: str

class ChatRepo:
    def list_for_recording(self, rid: int) -> list[ChatMessage]: ...
    def append(self, rid: int, role: str, content: str) -> int: ...
    def clear(self, rid: int) -> None: ...
```

## `chat.py` (Qt-free orchestrator)

```python
class ChatApiError(RuntimeError): ...
class ChatAuthError(ChatApiError): ...
class ChatTokenLimitError(ChatApiError): ...

TRANSCRIPT_CHAR_CEILING = 600_000   # match the summarizer's existing guard


def ask(
    db: Database,
    recording_id: int,
    user_message: str,
    *,
    api_key: str,
    model: str,
    anthropic_client_factory: Callable[[str], Any] | None = None,
) -> str:
    """Persist the user turn, call Claude with cached context + history,
    persist the reply, return it. Raises ChatAuthError on 401/403,
    ChatTokenLimitError when the transcript exceeds the ceiling, or
    ChatApiError otherwise.
    """
```

**System block** (cached):
```
You are answering questions about a meeting.

# Meeting
{display_title}    started {started_at}

# Summary
{summary.summary}

# Decisions
{summary.key_decisions joined}

# My todos
{summary.my_todos joined}

# Action items for others
{summary.action_items_others joined}

# Manual notes
{recording.manual_notes_as_plaintext}

# Transcript
{segments joined; each line: "[<mm:ss>] <ME|OTHERS>: <text>"}
```

Sections with no content are omitted. The whole block is one
`cache_control: {"type": "ephemeral"}` system entry.

**Messages array:** every prior `chat_messages` row converted to
`{"role": ..., "content": ...}`, followed by the new user turn.

**Anthropic call** uses the SAME `model` + `api_key` Settings as the
summarizer. `max_tokens=1024` for v1 (room for a detailed answer; cheap if
short).

## `ui/chat_card.py`

```python
class ChatCard(QFrame):
    send_requested = Signal(int, str)       # recording_id, user_text

    def __init__(self, recording_id: int,
                 history: list[ChatMessage],
                 *, enabled: bool = True,
                 disabled_hint: str | None = None,
                 parent: QWidget | None = None) -> None: ...

    def append_user_message(self, text: str) -> None: ...
    def append_assistant_message(self, text: str) -> None: ...
    def append_error_message(self, text: str) -> None: ...
    def set_pending(self, pending: bool) -> None: ...
```

Layout: themed card frame, header label "Chat about this meeting", a
scrollable `QScrollArea` of message bubbles, then a horizontal row with a
`QTextEdit` (1–4 lines auto-grow) and a primary **Send** button. Enter
sends; Shift+Enter newlines (QTextEdit's keyPressEvent override). Empty
state placeholder when history is empty: "Ask Claude about this meeting…".

**Disabled states:**

- No API key in keyring → `enabled=False`,
  `disabled_hint="Set your Anthropic API key in Settings → AI to chat."`
- Anthropic client error from a prior call → input re-enabled; the error
  appears as a styled red bubble in the message history.
- During an in-flight call → `set_pending(True)`: input disabled, Send
  button shows "Sending…".

## SummaryPane integration

In `show_recording`, after the existing button row, BEFORE the trailing
stretch, insert the chat card if (and only if) `TranscriptRepo.list_for_recording(rid)`
is non-empty:

```python
chat_history = ChatRepo(self._db).list_for_recording(rid)
api_key = self._anthropic_key_getter()    # injected like wrike_available
if api_key:
    self._chat_card = ChatCard(rid, chat_history, enabled=True)
else:
    self._chat_card = ChatCard(
        rid, chat_history, enabled=False,
        disabled_hint="Set your Anthropic API key in Settings → AI to chat.",
    )
self._chat_card.send_requested.connect(self.chat_send_requested.emit)
self._layout.addWidget(self._chat_card)
```

`SummaryPane` gains:
- `chat_send_requested = Signal(int, str)` — re-emit from the card.
- Constructor kwarg `anthropic_key_getter: Callable[[], str] | None = None`
  (mirrors the `wrike_available` pattern from v0.7.2).

## App handler

```python
class App:
    def __init__(self):
        ...
        self.summary = SummaryPane(
            self.db,
            wrike_available=self._wrike_is_configured,
            anthropic_key_getter=self._anthropic_key,
        )
        ...
        self.summary.chat_send_requested.connect(self._on_chat_send)

    def _anthropic_key(self) -> str:
        try:
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER_ANTHROPIC) or ""
        except Exception:
            return ""

    def _on_chat_send(self, recording_id: int, text: str) -> None:
        api_key = self._anthropic_key()
        if not api_key:
            return
        ChatRepo(self.db).append(recording_id, "user", text)
        self.summary._chat_card.append_user_message(text)
        self.summary._chat_card.set_pending(True)
        threading.Thread(
            target=self._chat_worker,
            args=(recording_id, text, api_key),
            daemon=True,
        ).start()

    def _chat_worker(self, rid: int, text: str, api_key: str) -> None:
        from PySide6.QtCore import QTimer
        from teams_transcriber.chat import ask, ChatApiError
        try:
            reply = ask(
                self.db, rid, text,
                api_key=api_key,
                model=self.settings.summary_model,
            )
            QTimer.singleShot(
                0, self.window,
                lambda r=reply: self._on_chat_done(rid, r),
            )
        except ChatApiError as exc:
            err = str(exc)
            QTimer.singleShot(
                0, self.window,
                lambda: self._on_chat_failed(rid, err),
            )

    def _on_chat_done(self, rid, reply): ...
    def _on_chat_failed(self, rid, err): ...
```

`_on_chat_done` and `_on_chat_failed` re-render only if the user is still
looking at the same recording (compare to `self.summary._current_recording_id`);
otherwise the card may have been torn down by a navigation. Even if not
visible, the messages are already persisted, so the next time the user
opens that meeting the reply appears in history.

## Failure modes

| Failure | Behavior |
|---|---|
| No API key in keyring | Card shown but disabled; input greyed out with "Set your Anthropic API key in Settings → AI to chat." |
| No transcript segments at all | Card NOT shown (no meaningful context to chat with). |
| Transcript exceeds 600 k chars | `ChatTokenLimitError` → red error bubble: "Transcript is too long to chat about — consider splitting the meeting." |
| Anthropic 401/403 | `ChatAuthError` → red bubble: "Anthropic key invalid; reset in Settings → AI." |
| Anthropic 429 / 5xx / network | `ChatApiError` → red bubble with the underlying message; user can retry. |
| Reply received while user navigated away | Persisted only; no UI update (we re-check current recording id). |

## Testing

### Unit (Qt-free)
- `storage/chat.py`: append + list + clear + cascade-delete with recording.
- `chat.py::ask` with a fake `anthropic_client_factory`:
  - persists user + assistant turns;
  - includes summary + notes + transcript text in the system block;
  - sends `cache_control: ephemeral` on the system block;
  - includes prior `chat_messages` rows in the messages array;
  - raises `ChatAuthError` on 401, `ChatTokenLimitError` on huge
    transcript, `ChatApiError` otherwise;
  - omits sections that are empty.

### Widget
- `ChatCard` smoke: builds with empty history (placeholder visible); after
  `append_user_message` / `append_assistant_message`, the new bubble shows
  in the scroll area; `set_pending(True)` disables Send.
- `ChatCard` disabled state: when `enabled=False`, the input is greyed and
  the disabled-hint label is visible; Send is disabled.
- `SummaryPane`: chat card is added iff transcript segments exist; signal
  is re-emitted via `chat_send_requested`.

### Manual
- Summary present + transcript present + API key configured → card appears,
  ask "what time did the meeting start?" → Claude answers from the
  transcript.
- Toggle a my-todo while chat reply is in flight → no crash, reply still
  arrives.
- Open a different meeting mid-flight → assistant reply persists to the
  original meeting's history (visible on revisit), no leakage.
- Delete a meeting → its chat history is gone via CASCADE (visible by
  re-creating the meeting and confirming the card is empty).

## Non-goals (deferred to a future phase)

- Streaming responses (chunked rendering as Claude generates).
- Search across chat histories (we don't index `chat_messages` text).
- Per-message edit / delete UI.
- "Clear chat" button (the orchestrator and repo support `clear`, but no
  button in v1; users can delete the meeting to wipe).
- Cross-meeting chat ("ask Claude about all meetings this week").
- Pinning or favoriting messages.
- Code-style syntax highlighting in replies; we render plain text only.

## Risks

| Risk | Mitigation |
|---|---|
| Cost on very long meetings | Prompt caching cuts repeat-turn input cost ~10×. The 600 k-char ceiling caps the worst case. |
| Background thread races with summary regeneration / navigation | Re-check `_current_recording_id` before re-rendering; persisted messages survive regardless. |
| API key handling | Same keyring pattern as everywhere; never accepted via chat input; chat input is for the *Claude conversation*, not the key. |
| Schema migration safety | Pure CREATE addition — no `recordings` CHECK changes, no rebuild needed. Cascade is symmetric with existing summaries / transcripts / wrike rows. |
