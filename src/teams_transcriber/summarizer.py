"""Anthropic Claude summarization with retry + structured output."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from teams_transcriber.config import Settings
from teams_transcriber.events import EventBus, SummaryReady
from teams_transcriber.storage import (
    ActionItemOther,
    Database,
    RecordingRepo,
    RecordingStatus,
    Summary,
    SummaryRepo,
    TodoItem,
    TodoStateRepo,
    TranscriptRepo,
)

logger = logging.getLogger(__name__)

SUMMARY_TOOL_NAME = "save_meeting_summary"

SYSTEM_PROMPT = """\
You summarize meeting transcripts produced by a Teams Transcriber app.

The transcript has two channels: "ME" (the user) and "OTHER" (the remote participants).
Use that distinction to attribute commitments accurately:
- `my_todos` = things the user (ME) committed to doing themselves.
- `action_items_others` = things other participants (OTHER) committed to doing.

Always call the save_meeting_summary tool with the full structured summary. Do not
respond with plain text. Be concise. Keep the one_line under 120 characters.
"""

TOOL_SCHEMA: dict[str, Any] = {
    "name": SUMMARY_TOOL_NAME,
    "description": "Save a structured summary of the meeting.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "one_line": {"type": "string"},
            "summary": {"type": "string"},
            "key_decisions": {"type": "array", "items": {"type": "string"}},
            "my_todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "context": {"type": ["string", "null"]},
                        "due": {"type": ["string", "null"]},
                    },
                    "required": ["task"],
                },
            },
            "action_items_others": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "who": {"type": "string"},
                        "task": {"type": "string"},
                        "due": {"type": ["string", "null"]},
                    },
                    "required": ["who", "task"],
                },
            },
            "follow_ups": {"type": "array", "items": {"type": "string"}},
            "topics": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "title", "one_line", "summary",
            "key_decisions", "my_todos", "action_items_others",
            "follow_ups", "topics",
        ],
    },
}


ClientFactory = Callable[[str], Any]


def _default_client_factory(api_key: str) -> Any:
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


class Summarizer:
    def __init__(
        self,
        *,
        bus: EventBus,
        db: Database,
        settings: Settings,
        client_factory: ClientFactory = _default_client_factory,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._bus = bus
        self._db = db
        self._settings = settings
        self._client_factory = client_factory
        self._sleep = sleep

    def summarize(self, recording_id: int, *, api_key: str | None) -> None:
        rec_repo = RecordingRepo(self._db)
        rec = rec_repo.get(recording_id)
        if rec is None:
            logger.error("summarize(%d): no such recording", recording_id)
            return
        if not api_key:
            rec_repo.update_status(
                recording_id, RecordingStatus.SUMMARY_FAILED,
                error_message="Anthropic API key is not configured",
            )
            return

        transcript = self._build_transcript_text(recording_id)
        if not transcript.strip():
            rec_repo.update_status(
                recording_id, RecordingStatus.SUMMARY_FAILED,
                error_message="transcript is empty",
            )
            return

        client = self._client_factory(api_key)
        result = self._call_with_retry(client, transcript, max_attempts=self._settings.ai_max_retries)
        if isinstance(result, Exception):
            rec_repo.update_status(
                recording_id, RecordingStatus.SUMMARY_FAILED, error_message=str(result),
            )
            return

        self._persist(recording_id, result)
        self._bus.publish(SummaryReady(recording_id=recording_id))

    # --- internals -------------------------------------------------------

    def _build_transcript_text(self, recording_id: int) -> str:
        segments = TranscriptRepo(self._db).list_for_recording(recording_id)
        lines = []
        for s in segments:
            ts = f"[{s.start_ms // 1000:>4}s]"
            who = "ME" if s.channel.value == "me" else "OTHER"
            lines.append(f"{ts} {who}: {s.text}")
        return "\n".join(lines)

    def _call_with_retry(
        self, client: Any, transcript: str, max_attempts: int,
    ) -> dict[str, Any] | Exception:
        """Returns the parsed tool payload on success, or the last Exception on failure."""
        addendum = self._settings.ai_custom_prompt_addendum
        sys_prompt = SYSTEM_PROMPT + ("\n\n" + addendum if addendum else "")
        last_err: Exception = RuntimeError("no attempts ran")
        for attempt in range(max_attempts):
            try:
                response = client.messages.create(
                    model=self._settings.ai_model,
                    max_tokens=4096,
                    system=sys_prompt,
                    tools=[TOOL_SCHEMA],
                    tool_choice={"type": "tool", "name": SUMMARY_TOOL_NAME},
                    messages=[{
                        "role": "user",
                        "content": f"Summarize this meeting:\n\n{transcript}",
                    }],
                )
                payload = self._extract_tool_input(response)
                if payload is None:
                    last_err = ValueError("response did not contain expected tool_use block")
                    if attempt < max_attempts - 1:
                        self._sleep(1.5 ** attempt)
                    continue
                return payload
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "summarize attempt %d/%d failed: %r", attempt + 1, max_attempts, exc,
                )
                if attempt < max_attempts - 1:
                    self._sleep(1.5 ** attempt)
        return last_err

    def _extract_tool_input(self, response: Any) -> dict[str, Any] | None:
        blocks = getattr(response, "content", []) or []
        for b in blocks:
            if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == SUMMARY_TOOL_NAME:
                payload = getattr(b, "input", None)
                if not isinstance(payload, dict):
                    return None
                required = TOOL_SCHEMA["input_schema"]["required"]
                if not all(k in payload for k in required):
                    return None
                return payload
        return None

    def _persist(self, recording_id: int, payload: dict[str, Any]) -> None:
        rec_repo = RecordingRepo(self._db)
        sum_repo = SummaryRepo(self._db)
        todo_repo = TodoStateRepo(self._db)
        summary = Summary(
            recording_id=recording_id,
            title=str(payload["title"]),
            one_line=str(payload["one_line"]),
            summary=str(payload["summary"]),
            key_decisions=list(payload["key_decisions"]),
            my_todos=[
                TodoItem(
                    task=str(d["task"]),
                    context=d.get("context"),
                    due=d.get("due"),
                )
                for d in payload["my_todos"]
            ],
            action_items_others=[
                ActionItemOther(
                    who=str(d["who"]),
                    task=str(d["task"]),
                    due=d.get("due"),
                )
                for d in payload["action_items_others"]
            ],
            follow_ups=list(payload["follow_ups"]),
            topics=list(payload["topics"]),
            generated_at=datetime.now(UTC).isoformat(),
            model_used=self._settings.ai_model,
        )
        sum_repo.upsert(summary)
        rec_repo.set_display_title(recording_id, summary.title or "Untitled meeting")
        rec_repo.update_status(recording_id, RecordingStatus.DONE)
        # Seed todo_state rows for each my_todo so the UI can toggle them.
        for i, td in enumerate(summary.my_todos):
            todo_repo.upsert(recording_id, todo_index=i, task_text=td.task, done=False)
