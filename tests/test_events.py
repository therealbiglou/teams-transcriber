from __future__ import annotations

import threading

from teams_transcriber.events import (
    EventBus,
    MeetingDetected,
    MeetingEnded,
    RecordingFailed,
    RecordingFinalized,
    RecordingStarted,
    SummaryReady,
    TranscriptionComplete,
)


def test_subscribe_and_publish_calls_handler() -> None:
    bus = EventBus()
    received: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, received.append)
    evt = MeetingDetected(window_title="Meeting | Microsoft Teams")
    bus.publish(evt)
    assert received == [evt]


def test_publish_with_no_subscribers_is_noop() -> None:
    bus = EventBus()
    bus.publish(MeetingEnded())  # must not raise


def test_multiple_handlers_each_receive_event() -> None:
    bus = EventBus()
    a: list[MeetingDetected] = []
    b: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, a.append)
    bus.subscribe(MeetingDetected, b.append)
    evt = MeetingDetected(window_title="X")
    bus.publish(evt)
    assert a == [evt]
    assert b == [evt]


def test_handler_exception_does_not_block_other_handlers() -> None:
    bus = EventBus()

    def boom(_e: MeetingDetected) -> None:
        raise RuntimeError("intentional")

    received: list[MeetingDetected] = []
    bus.subscribe(MeetingDetected, boom)
    bus.subscribe(MeetingDetected, received.append)
    bus.publish(MeetingDetected(window_title="X"))
    assert len(received) == 1  # second handler still ran


def test_unsubscribe_removes_handler() -> None:
    bus = EventBus()
    received: list[MeetingDetected] = []
    handler = received.append
    bus.subscribe(MeetingDetected, handler)
    bus.unsubscribe(MeetingDetected, handler)
    bus.publish(MeetingDetected(window_title="X"))
    assert received == []


def test_different_event_types_are_isolated() -> None:
    bus = EventBus()
    md: list[MeetingDetected] = []
    me: list[MeetingEnded] = []
    bus.subscribe(MeetingDetected, md.append)
    bus.subscribe(MeetingEnded, me.append)
    bus.publish(MeetingDetected(window_title="X"))
    assert len(md) == 1
    assert me == []


def test_publish_is_thread_safe() -> None:
    bus = EventBus()
    counter = 0
    lock = threading.Lock()

    def handler(_e: MeetingDetected) -> None:
        nonlocal counter
        with lock:
            counter += 1

    bus.subscribe(MeetingDetected, handler)

    def worker() -> None:
        for _ in range(100):
            bus.publish(MeetingDetected(window_title="X"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert counter == 400


def test_event_dataclasses_carry_expected_fields() -> None:
    """Smoke check that the event types have the fields callers will use."""
    MeetingDetected(window_title="X")
    MeetingEnded()
    RecordingStarted(recording_id=1, audio_path="C:/a.opus")
    RecordingFinalized(recording_id=1, duration_ms=12345)
    RecordingFailed(recording_id=1, error_message="boom")
    TranscriptionComplete(recording_id=1, segment_count=10)
    SummaryReady(recording_id=1)


def test_live_segment_available_event_round_trip() -> None:
    from teams_transcriber.events import LiveSegmentAvailable
    from teams_transcriber.storage.models import Channel, TranscriptSegment

    seg = TranscriptSegment(
        id=1, recording_id=42, start_ms=1000, end_ms=2000,
        channel=Channel.ME, text="hello",
    )
    evt = LiveSegmentAvailable(recording_id=42, segment=seg)
    assert evt.recording_id == 42
    assert evt.segment.text == "hello"


def test_live_transcription_degraded_event_round_trip() -> None:
    from teams_transcriber.events import LiveTranscriptionDegraded

    evt = LiveTranscriptionDegraded(recording_id=7, reason="cuda oom")
    assert evt.recording_id == 7
    assert evt.reason == "cuda oom"
