from __future__ import annotations

import threading

from teams_transcriber.events import (
    EventBus,
    MeetingDetected,
    RecordingFinalized,
    SummaryReady,
)
from teams_transcriber.ui.qt_bridge import QtEventBridge


def test_bus_event_arrives_on_qt_signal(qapp, qtbot) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)

    received: list[str] = []
    bridge.meeting_detected.connect(lambda evt: received.append(evt.window_title))

    with qtbot.waitSignal(bridge.meeting_detected, timeout=1000):
        bus.publish(MeetingDetected(window_title="X"))

    assert received == ["X"]


def test_bridge_emits_all_event_types(qapp, qtbot) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)

    finalized: list[int] = []
    bridge.recording_finalized.connect(lambda evt: finalized.append(evt.recording_id))
    summary: list[int] = []
    bridge.summary_ready.connect(lambda evt: summary.append(evt.recording_id))

    with qtbot.waitSignal(bridge.recording_finalized, timeout=1000):
        bus.publish(RecordingFinalized(recording_id=1, duration_ms=1000))
    with qtbot.waitSignal(bridge.summary_ready, timeout=1000):
        bus.publish(SummaryReady(recording_id=1))

    assert finalized == [1]
    assert summary == [1]


def test_cross_thread_publish_arrives_on_main_thread(qapp, qtbot) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)

    received_on: list[int] = []
    main_tid = threading.get_ident()
    bridge.meeting_detected.connect(lambda _evt: received_on.append(threading.get_ident()))

    def publish_from_worker() -> None:
        bus.publish(MeetingDetected(window_title="from worker"))

    t = threading.Thread(target=publish_from_worker, daemon=True)
    with qtbot.waitSignal(bridge.meeting_detected, timeout=1000):
        t.start()
    t.join()

    assert received_on == [main_tid]
