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


def test_bridge_emits_live_segment_available(qapp) -> None:
    from teams_transcriber.events import EventBus, LiveSegmentAvailable
    from teams_transcriber.storage.models import Channel, TranscriptSegment
    from teams_transcriber.ui.qt_bridge import QtEventBridge

    bus = EventBus()
    bridge = QtEventBridge(bus)
    received: list[LiveSegmentAvailable] = []
    bridge.live_segment_available.connect(received.append)

    seg = TranscriptSegment(
        id=None, recording_id=1, start_ms=0, end_ms=1500,
        channel=Channel.ME, text="bridge me",
    )
    bus.publish(LiveSegmentAvailable(recording_id=1, segment=seg))
    qapp.processEvents()
    assert received and received[0].segment.text == "bridge me"


def test_bridge_emits_live_transcription_degraded(qapp) -> None:
    from teams_transcriber.events import EventBus, LiveTranscriptionDegraded
    from teams_transcriber.ui.qt_bridge import QtEventBridge

    bus = EventBus()
    bridge = QtEventBridge(bus)
    received: list[LiveTranscriptionDegraded] = []
    bridge.live_transcription_degraded.connect(received.append)

    bus.publish(LiveTranscriptionDegraded(recording_id=5, reason="ouch"))
    qapp.processEvents()
    assert received and received[0].reason == "ouch"


def test_bridge_emits_recording_device_fallback(qapp) -> None:
    from teams_transcriber.events import EventBus, RecordingDeviceFallback
    from teams_transcriber.ui.qt_bridge import QtEventBridge

    bus = EventBus()
    bridge = QtEventBridge(bus)
    received: list[RecordingDeviceFallback] = []
    bridge.recording_device_fallback.connect(received.append)

    bus.publish(RecordingDeviceFallback(
        recording_id=42, channel="microphone", requested_name="Sony Headset",
    ))
    qapp.processEvents()
    assert received and received[0].requested_name == "Sony Headset"
