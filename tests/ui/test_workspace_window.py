"""Tests for the WorkspaceWindow."""

from __future__ import annotations

import pytest

from teams_transcriber.config import load_settings
from teams_transcriber.events import EventBus, LiveSegmentAvailable
from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import (
    Channel,
    Recording,
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    TranscriptRepo,
    TranscriptSegment,
    build_database,
)
from teams_transcriber.ui.qt_bridge import QtEventBridge
from teams_transcriber.ui.workspace_window import WorkspaceWindow


@pytest.fixture
def env(tmp_path, qapp):
    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    settings = load_settings(paths)
    yield paths, db, settings
    db.close()


def _make_recording(db, *, status: RecordingStatus) -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-05-18T10:00:00+00:00",
        ended_at=None, source=RecordingSource.MANUAL,
        detected_title="t", display_title="t", audio_path=None,
        audio_deleted_at=None, duration_ms=None,
        status=status, error_message=None,
    ))
    assert rec.id is not None
    return rec.id


def test_workspace_opens_in_live_mode_and_appends_segments(env, qapp) -> None:
    paths, db, settings = env
    bus = EventBus()
    bridge = QtEventBridge(bus)
    rid = _make_recording(db, status=RecordingStatus.RECORDING)
    win = WorkspaceWindow(db=db, recording_id=rid, bridge=bridge, live=True)

    bus.publish(LiveSegmentAvailable(
        recording_id=rid,
        segment=TranscriptSegment(
            id=None, recording_id=rid, start_ms=0, end_ms=1500,
            channel=Channel.ME, text="hello live",
        ),
    ))
    qapp.processEvents()
    assert win.transcript_view.count() == 1


def test_workspace_ignores_segments_for_other_recordings(env, qapp) -> None:
    paths, db, settings = env
    bus = EventBus()
    bridge = QtEventBridge(bus)
    rid = _make_recording(db, status=RecordingStatus.RECORDING)
    win = WorkspaceWindow(db=db, recording_id=rid, bridge=bridge, live=True)

    bus.publish(LiveSegmentAvailable(
        recording_id=rid + 99,
        segment=TranscriptSegment(
            id=None, recording_id=rid + 99, start_ms=0, end_ms=1500,
            channel=Channel.ME, text="for another recording",
        ),
    ))
    qapp.processEvents()
    assert win.transcript_view.count() == 0


def test_workspace_past_mode_loads_existing_segments_no_subscription(env, qapp) -> None:
    paths, db, settings = env
    bus = EventBus()
    bridge = QtEventBridge(bus)
    rid = _make_recording(db, status=RecordingStatus.DONE)
    TranscriptRepo(db).append(TranscriptSegment(
        id=None, recording_id=rid, start_ms=0, end_ms=1500,
        channel=Channel.ME, text="historical",
    ))
    win = WorkspaceWindow(db=db, recording_id=rid, bridge=bridge, live=False)
    assert win.transcript_view.count() == 1
    bus.publish(LiveSegmentAvailable(
        recording_id=rid,
        segment=TranscriptSegment(
            id=None, recording_id=rid, start_ms=1500, end_ms=3000,
            channel=Channel.OTHERS, text="newer",
        ),
    ))
    qapp.processEvents()
    assert win.transcript_view.count() == 1


def test_workspace_stop_button_emits_signal(env, qapp) -> None:
    paths, db, settings = env
    bus = EventBus()
    bridge = QtEventBridge(bus)
    rid = _make_recording(db, status=RecordingStatus.RECORDING)
    win = WorkspaceWindow(db=db, recording_id=rid, bridge=bridge, live=True)
    received: list[int] = []
    win.stop_recording_requested.connect(received.append)
    win._stop_button.click()
    assert received == [rid]


def test_workspace_set_recording_finished_hides_stop_button(env, qapp) -> None:
    paths, db, settings = env
    bus = EventBus()
    bridge = QtEventBridge(bus)
    rid = _make_recording(db, status=RecordingStatus.RECORDING)
    win = WorkspaceWindow(db=db, recording_id=rid, bridge=bridge, live=True)
    assert win._stop_button.isVisible() is True or win._stop_button.isVisibleTo(win)
    win.set_recording_finished()
    assert win._stop_button.isHidden() is True


def test_workspace_shows_placeholder_when_live_disabled(env, qapp) -> None:
    """In live recording mode but with live_enabled=False, show a placeholder
    instead of subscribing to LiveSegmentAvailable."""
    from teams_transcriber.events import EventBus, LiveSegmentAvailable
    from teams_transcriber.storage import (
        Channel,
        RecordingStatus,
        TranscriptSegment,
    )
    from teams_transcriber.ui.qt_bridge import QtEventBridge
    from teams_transcriber.ui.workspace_window import WorkspaceWindow

    paths, db, settings = env
    settings._raw["transcription"]["live_enabled"] = False
    bus = EventBus()
    bridge = QtEventBridge(bus)
    rid = _make_recording(db, status=RecordingStatus.RECORDING)
    win = WorkspaceWindow(
        db=db, recording_id=rid, bridge=bridge, live=True, settings=settings,
    )
    bus.publish(LiveSegmentAvailable(
        recording_id=rid,
        segment=TranscriptSegment(
            id=None, recording_id=rid, start_ms=0, end_ms=1500,
            channel=Channel.ME, text="should be ignored",
        ),
    ))
    qapp.processEvents()
    assert win.transcript_view.count() == 0
