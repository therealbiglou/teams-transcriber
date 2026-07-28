# tests/ui/test_meeting_status.py
from __future__ import annotations

import pytest

from teams_transcriber.storage.models import RecordingStatus
from teams_transcriber.ui.meeting_status import RowAction, derive_row_state


@pytest.mark.parametrize("status", [
    RecordingStatus.RECORDING,
    RecordingStatus.TRANSCRIBING,
    RecordingStatus.SUMMARIZING,
    RecordingStatus.WAITING_FOR_NOTES,
])
def test_in_progress_shows_processing_and_no_action(status):
    state = derive_row_state(status=status)
    assert state.chip == "Processing…"
    assert state.action is RowAction.NONE
    assert state.action_label is None
    assert state.error_message is None


@pytest.mark.parametrize("status", [
    RecordingStatus.TRANSCRIPTION_FAILED,
    RecordingStatus.SUMMARY_FAILED,
])
def test_pipeline_failure_is_retryable_and_clickable(status):
    state = derive_row_state(status=status, error_message="boom")
    assert state.chip == "Failed"
    assert state.action is RowAction.RETRY
    assert state.action_label == "Retry"
    assert state.error_message == "boom"


def test_recording_failure_is_clickable_but_not_retryable():
    state = derive_row_state(
        status=RecordingStatus.RECORDING_FAILED, error_message="no mic",
    )
    assert state.chip == "Recording failed"
    assert state.action is RowAction.NONE
    assert state.action_label is None
    assert state.error_message == "no mic"


def test_done_without_wrike_offers_send():
    state = derive_row_state(status=RecordingStatus.DONE)
    assert state.chip == "Not in Wrike"
    assert state.action is RowAction.SEND_TO_WRIKE
    assert state.action_label == "Send to Wrike"
    assert state.error_message is None


def test_done_with_permalink_offers_open():
    state = derive_row_state(
        status=RecordingStatus.DONE,
        has_wrike_project=True,
        wrike_permalink="https://www.wrike.com/open.htm?id=1",
        wrike_sync_status="synced",
    )
    assert state.chip == "In Wrike"
    assert state.action is RowAction.OPEN_IN_WRIKE
    assert state.action_label == "Open in Wrike"


def test_wrike_failure_is_clickable_and_retries_the_push():
    state = derive_row_state(
        status=RecordingStatus.DONE,
        has_wrike_project=True,
        wrike_permalink="https://w/1",
        wrike_sync_status="failed",
        wrike_error_message="401 unauthorized",
    )
    assert state.chip == "Wrike failed"
    assert state.action is RowAction.SEND_TO_WRIKE
    assert state.action_label == "Retry"
    assert state.error_message == "401 unauthorized"


def test_project_without_permalink_falls_back_to_send():
    """A re-push is idempotent and repairs the missing permalink."""
    state = derive_row_state(
        status=RecordingStatus.DONE, has_wrike_project=True, wrike_permalink=None,
    )
    assert state.chip == "Not in Wrike"
    assert state.action is RowAction.SEND_TO_WRIKE
