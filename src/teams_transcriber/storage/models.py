"""Dataclasses and enums representing storage rows.

Dataclasses are used (not Pydantic) because storage rows are tiny and we don't want
runtime validation overhead. Validation lives at app boundaries, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecordingSource(StrEnum):
    TEAMS = "teams"
    MANUAL = "manual"


class RecordingStatus(StrEnum):
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    WAITING_FOR_NOTES = "waiting_for_notes"
    DONE = "done"
    RECORDING_FAILED = "recording_failed"
    TRANSCRIPTION_FAILED = "transcription_failed"
    SUMMARY_FAILED = "summary_failed"


class Channel(StrEnum):
    ME = "me"
    OTHERS = "others"


@dataclass(slots=True)
class Recording:
    id: int | None
    started_at: str  # ISO 8601 UTC
    ended_at: str | None
    source: RecordingSource
    detected_title: str | None
    display_title: str | None
    audio_path: str | None
    audio_deleted_at: str | None
    duration_ms: int | None
    status: RecordingStatus
    error_message: str | None
    manual_notes: str | None = None  # HTML-formatted user notes, included in AI prompt


@dataclass(slots=True)
class TranscriptSegment:
    id: int | None
    recording_id: int
    start_ms: int
    end_ms: int
    channel: Channel
    text: str


@dataclass(slots=True)
class TodoItem:
    task: str
    context: str | None = None
    due: str | None = None  # ISO date string or None


@dataclass(slots=True)
class ActionItemOther:
    who: str
    task: str
    due: str | None = None


@dataclass(slots=True)
class Summary:
    recording_id: int
    title: str | None
    one_line: str | None
    summary: str | None
    key_decisions: list[str]
    my_todos: list[TodoItem]
    action_items_others: list[ActionItemOther]
    follow_ups: list[str]
    topics: list[str]
    generated_at: str
    model_used: str


@dataclass(slots=True)
class TodoState:
    id: int | None
    recording_id: int
    todo_index: int
    task_text: str
    done: bool
    done_at: str | None
