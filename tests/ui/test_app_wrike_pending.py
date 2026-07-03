"""Tests for _wrike_pick_pending helper (startup pending-Wrike-syncs toast)."""


def test_pending_retry_picks_oldest_first():
    from teams_transcriber.ui.app import _wrike_pick_pending
    from teams_transcriber.storage.wrike import WrikeSyncRow
    rows = [
        WrikeSyncRow(recording_id=3, folder_id=None, status="pending",
                     last_attempted_at="2026-06-01", error_message=None),
        WrikeSyncRow(recording_id=1, folder_id=None, status="failed",
                     last_attempted_at="2026-05-30", error_message="boom"),
        WrikeSyncRow(recording_id=2, folder_id="F", status="synced",
                     last_attempted_at="2026-06-02", error_message=None),
    ]
    assert _wrike_pick_pending(rows) == 1   # earliest among pending+failed


def test_pending_retry_returns_none_when_empty():
    from teams_transcriber.ui.app import _wrike_pick_pending
    assert _wrike_pick_pending([]) is None


def test_pending_retry_ignores_synced_rows():
    from teams_transcriber.ui.app import _wrike_pick_pending
    from teams_transcriber.storage.wrike import WrikeSyncRow
    rows = [
        WrikeSyncRow(recording_id=1, folder_id="F", status="synced",
                     last_attempted_at="2026-05-01", error_message=None),
        WrikeSyncRow(recording_id=2, folder_id="F", status="skipped",
                     last_attempted_at="2026-04-01", error_message=None),
    ]
    assert _wrike_pick_pending(rows) is None
