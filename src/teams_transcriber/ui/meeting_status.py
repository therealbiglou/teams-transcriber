"""Derives what one history row shows: a status chip and its action.

Pure — no Qt, no database — so every state combination is unit-testable.
``RowState.error_message`` is non-None exactly when the chip is clickable
(clicking it opens the failure detail dialog).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from teams_transcriber.storage.models import RecordingStatus

_IN_PROGRESS = frozenset({
    RecordingStatus.RECORDING,
    RecordingStatus.TRANSCRIBING,
    RecordingStatus.SUMMARIZING,
    RecordingStatus.WAITING_FOR_NOTES,
})

_RETRYABLE_FAILURES = frozenset({
    RecordingStatus.TRANSCRIPTION_FAILED,
    RecordingStatus.SUMMARY_FAILED,
})


class RowAction(StrEnum):
    NONE = "none"
    RETRY = "retry"
    SEND_TO_WRIKE = "send_to_wrike"
    OPEN_IN_WRIKE = "open_in_wrike"


@dataclass(frozen=True, slots=True)
class RowState:
    chip: str
    action: RowAction
    action_label: str | None
    error_message: str | None


def derive_row_state(
    *,
    status: RecordingStatus,
    error_message: str | None = None,
    has_wrike_project: bool = False,
    wrike_permalink: str | None = None,
    wrike_sync_status: str | None = None,
    wrike_error_message: str | None = None,
) -> RowState:
    if status in _IN_PROGRESS:
        return RowState("Processing…", RowAction.NONE, None, None)

    if status in _RETRYABLE_FAILURES:
        return RowState("Failed", RowAction.RETRY, "Retry", error_message)

    if status is RecordingStatus.RECORDING_FAILED:
        # The audio was never captured, so there is nothing to re-run.
        return RowState("Recording failed", RowAction.NONE, None, error_message)

    # status is DONE from here on.
    if wrike_sync_status == "failed":
        return RowState(
            "Wrike failed", RowAction.SEND_TO_WRIKE, "Retry", wrike_error_message,
        )

    if has_wrike_project and wrike_permalink:
        return RowState("In Wrike", RowAction.OPEN_IN_WRIKE, "Open in Wrike", None)

    # No project, or a project row with no usable permalink — a re-push is
    # idempotent and repairs the record.
    return RowState("Not in Wrike", RowAction.SEND_TO_WRIKE, "Send to Wrike", None)
