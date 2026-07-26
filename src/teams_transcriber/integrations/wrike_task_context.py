"""Transcript-grounded context blurbs for exported Wrike tasks.

Each to-do / action-item / follow-up exported to Wrike (see
``wrike_project_export.py``) can carry a short plain-text *description*
summarizing what was actually said about it in the transcript, so the task
is understandable in Wrike without opening the meeting.

This is one single batched Claude tool-use call for the whole meeting (not
one call per item -- that would be slow and expensive). The caller passes
an already-constructed Anthropic client (same pattern as
``summarizer.py``'s ``client_factory(api_key)`` result) -- this module does
not touch API keys.

Degrades to ``{}`` on ANY failure (network error, malformed/missing tool
response, empty transcript, etc.) -- callers then create tasks with no
description, exactly as before this feature existed. Never raises.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from teams_transcriber.storage.models import Summary, TranscriptSegment

logger = logging.getLogger(__name__)

# Keep the transcript payload sane for very long meetings. At ~4 chars/token
# this is ~15k tokens -- plenty for the model to ground a 2-3 sentence blurb
# per item without blowing the request budget.
_TRANSCRIPT_CHAR_BUDGET = 60_000

_TOOL_NAME = "save_task_contexts"
_VALID_KINDS = {"my", "other", "follow_up"}

_TOOL: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Save a short context blurb for each to-do / action-item / follow-up, "
        "summarizing what was actually said about it in the meeting transcript."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "contexts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": sorted(_VALID_KINDS)},
                        "index": {"type": "integer"},
                        "context": {"type": "string"},
                    },
                    "required": ["kind", "index", "context"],
                },
            },
        },
        "required": ["contexts"],
    },
}


def _transcript_text(segments: list[TranscriptSegment]) -> str:
    lines = []
    for s in segments:
        ts = f"[{s.start_ms // 1000:>4}s]"
        who = "ME" if s.channel.value == "me" else "OTHER"
        lines.append(f"{ts} {who}: {s.text}")
    return "\n".join(lines)


def _enumerate_items(summary: Summary) -> list[tuple[str, int, str]]:
    """Same enumeration order/kinds as ``wrike_project_export._ensure_task``."""
    items: list[tuple[str, int, str]] = []
    for i, td in enumerate(summary.my_todos):
        items.append(("my", i, td.task))
    for j, ai in enumerate(summary.action_items_others):
        title = f"{ai.who}: {ai.task}" if ai.who else ai.task
        items.append(("other", j, title))
    for k, f in enumerate(summary.follow_ups):
        items.append(("follow_up", k, f))
    return items


def _extract_tool_payload(response: Any) -> dict[str, Any] | None:
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) != "tool_use" or getattr(block, "name", "") != _TOOL_NAME:
            continue
        raw = getattr(block, "input", None)
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def build_task_contexts(
    client: Any,
    *,
    summary: Summary,
    segments: list[TranscriptSegment],
    model: str,
    max_tokens: int = 4096,
    char_budget: int = _TRANSCRIPT_CHAR_BUDGET,
) -> dict[tuple[str, int], str]:
    """One batched Claude call: {(kind, index): context_text} for every item.

    ``kind`` is one of "my" / "other" / "follow_up", matching
    ``wrike_project_export``'s task kinds; ``index`` is the item's position
    in the corresponding summary list. Any exception, or a response missing
    the expected tool_use block, yields ``{}`` -- callers then create tasks
    with no description.
    """
    items = _enumerate_items(summary)
    if not items:
        return {}

    try:
        valid_keys = {(kind, idx) for kind, idx, _ in items}
        items_block = "\n".join(
            f"- kind={kind} index={idx} item={text!r}" for kind, idx, text in items
        )
        transcript = _transcript_text(segments)[:char_budget]
        user_text = (
            "Meeting transcript (may be truncated; \"ME\" is the app's user, "
            "\"OTHER\" is remote participants):\n\n"
            f"{transcript}\n\n"
            "Items needing context (one per to-do / action-item / follow-up "
            "from the meeting summary):\n"
            f"{items_block}\n\n"
            "For EVERY item above, call save_task_contexts with a short (2-3 "
            "sentence) plain-text context blurb drawn from the transcript, "
            "explaining what was actually said about it so the task is "
            "understandable in Wrike without opening the meeting. Do not use "
            "markdown or HTML in the context text."
        )
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user_text}],
        )
        payload = _extract_tool_payload(response)
        if payload is None:
            logger.warning("wrike task-context call returned no usable tool_use block")
            return {}

        out: dict[tuple[str, int], str] = {}
        for entry in payload.get("contexts", []) or []:
            try:
                kind = str(entry["kind"])
                index = int(entry["index"])
                context = str(entry["context"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if kind not in _VALID_KINDS or (kind, index) not in valid_keys or not context:
                continue
            out[(kind, index)] = context
        return out
    except Exception:
        logger.warning("wrike task-context generation failed", exc_info=True)
        return {}
