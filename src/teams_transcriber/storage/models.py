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
