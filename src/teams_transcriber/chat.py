"""Per-meeting chat with Claude.

`ask(db, recording_id, user_text, ...)` persists the user turn, calls Claude
with a prompt-cached system block (summary + manual notes + full transcript)
+ the persisted conversation history, persists the assistant reply, returns
it. Qt-free.
"""

from __future__ import annotations

import html as _html
import logging
import re
from collections.abc import Callable
from typing import Any

import anthropic

from teams_transcriber.storage.chat import ChatRepo
from teams_transcriber.storage.db import Database
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.summaries import SummaryRepo
from teams_transcriber.storage.transcripts import TranscriptRepo
from teams_transcriber.storage.models import Channel, TranscriptSegment

logger = logging.getLogger(__name__)

TRANSCRIPT_CHAR_CEILING = 600_000   # matches the summarizer's existing guard
_MAX_TOKENS = 1024
_TAG_RE = re.compile(r"<[^>]+>")


class ChatApiError(RuntimeError):
    """Generic chat failure (network, 5xx, etc.)."""


class ChatAuthError(ChatApiError):
    """401/403 — Anthropic key missing or invalid."""


class ChatTokenLimitError(ChatApiError):
    """Transcript exceeded the chat-input ceiling."""


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    return _html.unescape(_TAG_RE.sub("", s)).strip()


def _fmt_segment(seg: TranscriptSegment) -> str:
    total = max(0, seg.start_ms // 1000)
    ts = f"{total // 60:02d}:{total % 60:02d}"
    speaker = "ME" if seg.channel == Channel.ME else "OTHERS"
    return f"[{ts}] {speaker}: {seg.text}"


def _build_system_text(db: Database, recording_id: int) -> str:
    rec = RecordingRepo(db).get(recording_id)
    summary = SummaryRepo(db).get(recording_id)
    segments = TranscriptRepo(db).list_for_recording(recording_id)
    transcript_text = "\n".join(_fmt_segment(s) for s in segments)
    if len(transcript_text) > TRANSCRIPT_CHAR_CEILING:
        raise ChatTokenLimitError(
            f"Transcript is {len(transcript_text)} characters — over the "
            f"{TRANSCRIPT_CHAR_CEILING}-character limit. Split the meeting "
            "or shorten the transcript to chat about it."
        )

    parts: list[str] = ["You are answering questions about a meeting."]
    title = (rec.display_title if rec else None) or (
        summary.title if summary else None) or "Meeting"
    started_at = rec.started_at if rec else ""
    parts.append(f"# Meeting\n{title}    started {started_at}")
    if summary is not None:
        if summary.summary:
            parts.append(f"# Summary\n{summary.summary}")
        if summary.key_decisions:
            parts.append("# Decisions\n" + "\n".join(
                f"- {d}" for d in summary.key_decisions
            ))
        if summary.my_todos:
            parts.append("# My todos\n" + "\n".join(
                f"- {t.task}" + (f" (due {t.due})" if t.due else "")
                for t in summary.my_todos
            ))
        if summary.action_items_others:
            parts.append("# Action items for others\n" + "\n".join(
                f"- {a.who}: {a.task}" + (f" (due {a.due})" if a.due else "")
                for a in summary.action_items_others
            ))
    notes = _strip_html(rec.manual_notes if rec else None)
    if notes:
        parts.append(f"# Manual notes\n{notes}")
    parts.append(f"# Transcript\n{transcript_text}")
    return "\n\n".join(parts)


def _default_client_factory(api_key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key)


def ask(
    db: Database,
    recording_id: int,
    user_text: str,
    *,
    api_key: str,
    model: str,
    anthropic_client_factory: Callable[[str], Any] | None = None,
) -> str:
    """Persist the user turn, call Claude, persist the reply, return it."""
    repo = ChatRepo(db)
    # Persist FIRST so a failed API call still records what the user asked.
    repo.append(recording_id, "user", user_text)

    # Build context. May raise ChatTokenLimitError.
    system_text = _build_system_text(db, recording_id)
    history = repo.list_for_recording(recording_id)
    messages = [{"role": m.role, "content": m.content} for m in history]

    factory = anthropic_client_factory or _default_client_factory
    client = factory(api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=[{
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=messages,
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise ChatAuthError(str(exc) or "Anthropic auth failed") from exc
    except anthropic.APIError as exc:
        raise ChatApiError(str(exc) or "Anthropic API error") from exc
    except Exception as exc:
        logger.exception("chat call failed for recording_id=%d", recording_id)
        raise ChatApiError(str(exc) or "chat request failed") from exc

    reply_text = ""
    for block in response.content or []:
        if getattr(block, "type", None) == "text":
            reply_text += block.text
    reply_text = reply_text.strip() or "(empty reply)"
    repo.append(recording_id, "assistant", reply_text)
    return reply_text
