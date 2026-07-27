"""Tests for the transcript-grounded task-context generator."""

from __future__ import annotations

from typing import Any

import pytest

from teams_transcriber.integrations.wrike_task_context import (
    _MAX_RETRIES,
    _REQUEST_TIMEOUT_SECONDS,
    TaskCopy,
    build_task_contexts,
)
from teams_transcriber.storage.models import (
    ActionItemOther,
    Channel,
    Summary,
    TodoItem,
    TranscriptSegment,
)


def _summary(**kw: Any) -> Summary:
    base = dict(
        recording_id=1, title="T", one_line="one", summary="The summary body.",
        key_decisions=[], my_todos=[], action_items_others=[], follow_ups=[],
        topics=[], generated_at="2026-07-24T00:00:00+00:00", model_used="m",
    )
    base.update(kw)
    return Summary(**base)


def _segments() -> list[TranscriptSegment]:
    return [
        TranscriptSegment(id=None, recording_id=1, start_ms=0, end_ms=1000,
                          channel=Channel.ME, text="I'll write the shot list."),
        TranscriptSegment(id=None, recording_id=1, start_ms=1000, end_ms=2000,
                          channel=Channel.OTHERS, text="Sam will send the doc."),
    ]


class _FakeBlock:
    def __init__(self, name: str, input_: Any) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = input_


class _FakeResp:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessages:
    def __init__(self, scripted: _FakeResp | Exception) -> None:
        self._scripted = scripted
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResp:
        self.calls.append(kwargs)
        if isinstance(self._scripted, Exception):
            raise self._scripted
        return self._scripted


class _FakeClient:
    def __init__(
        self, scripted: _FakeResp | Exception, *, raise_on_with_options: bool = False,
    ) -> None:
        self.messages = _FakeMessages(scripted)
        self.with_options_calls: list[dict[str, Any]] = []
        self._raise_on_with_options = raise_on_with_options

    def with_options(self, **kwargs: Any) -> _FakeClient:
        self.with_options_calls.append(kwargs)
        if self._raise_on_with_options:
            raise RuntimeError("with_options boom")
        return self


def _summary_with_items() -> Summary:
    return _summary(
        my_todos=[TodoItem(task="Write shot list"), TodoItem(task="Book studio")],
        action_items_others=[ActionItemOther(who="Sam", task="Send doc")],
        follow_ups=["Revisit pricing"],
    )


def test_maps_contexts_to_kind_index_pairs() -> None:
    fake = _FakeResp(content=[
        _FakeBlock(
            name="save_task_contexts",
            input_={
                "contexts": [
                    {"kind": "my", "index": 0, "title": "Write shot list",
                     "context": "You committed to the shot list."},
                    {"kind": "other", "index": 0, "title": "Send the doc",
                     "context": "Sam agreed to send the doc."},
                    {"kind": "follow_up", "index": 0, "title": "Revisit pricing",
                     "context": "Pricing needs revisiting."},
                ],
            },
        ),
    ])
    client = _FakeClient(fake)
    out = build_task_contexts(
        client, summary=_summary_with_items(), segments=_segments(), model="claude-sonnet-4-6",
    )
    assert out == {
        ("my", 0): TaskCopy(title="Write shot list", context="You committed to the shot list."),
        ("other", 0): TaskCopy(title="Send the doc", context="Sam agreed to send the doc."),
        ("follow_up", 0): TaskCopy(title="Revisit pricing", context="Pricing needs revisiting."),
    }
    assert len(client.messages.calls) == 1
    # The call must carry an explicit timeout + capped retries -- the SDK
    # default (~600s, up to 2 retries) is what made a stalled export look hung.
    assert client.with_options_calls == [
        {"timeout": _REQUEST_TIMEOUT_SECONDS, "max_retries": _MAX_RETRIES},
    ]


def test_unknown_or_out_of_range_entries_are_dropped() -> None:
    fake = _FakeResp(content=[
        _FakeBlock(
            name="save_task_contexts",
            input_={
                "contexts": [
                    {"kind": "my", "index": 0, "title": "Valid", "context": "Valid."},
                    {"kind": "my", "index": 99, "title": "Out of range",
                     "context": "Out of range index."},
                    {"kind": "bogus", "index": 0, "title": "Unknown", "context": "Unknown kind."},
                    {"kind": "other", "index": 5, "title": "Also out of range",
                     "context": "Also out of range."},
                ],
            },
        ),
    ])
    client = _FakeClient(fake)
    out = build_task_contexts(
        client, summary=_summary_with_items(), segments=_segments(), model="claude-sonnet-4-6",
    )
    assert out == {("my", 0): TaskCopy(title="Valid", context="Valid.")}


def test_blank_or_missing_title_becomes_none() -> None:
    fake = _FakeResp(content=[
        _FakeBlock(
            name="save_task_contexts",
            input_={
                "contexts": [
                    {"kind": "my", "index": 0, "title": "   ", "context": "Blank title."},
                    {"kind": "other", "index": 0, "context": "Missing title key entirely."},
                ],
            },
        ),
    ])
    client = _FakeClient(fake)
    out = build_task_contexts(
        client, summary=_summary_with_items(), segments=_segments(), model="claude-sonnet-4-6",
    )
    assert out == {
        ("my", 0): TaskCopy(title=None, context="Blank title."),
        ("other", 0): TaskCopy(title=None, context="Missing title key entirely."),
    }


def test_trailing_period_is_stripped_from_title() -> None:
    fake = _FakeResp(content=[
        _FakeBlock(
            name="save_task_contexts",
            input_={
                "contexts": [
                    {"kind": "my", "index": 0, "title": "Confirm pre-con site visit.",
                     "context": "Some context."},
                ],
            },
        ),
    ])
    client = _FakeClient(fake)
    out = build_task_contexts(
        client, summary=_summary_with_items(), segments=_segments(), model="claude-sonnet-4-6",
    )
    assert out[("my", 0)].title == "Confirm pre-con site visit"


def test_overlong_title_is_truncated_on_word_boundary() -> None:
    long_title = "Confirm availability for the pre-con site visit " + ("word " * 10) + "tail"
    assert len(long_title) > 80
    fake = _FakeResp(content=[
        _FakeBlock(
            name="save_task_contexts",
            input_={
                "contexts": [
                    {"kind": "my", "index": 0, "title": long_title, "context": "Some context."},
                ],
            },
        ),
    ])
    client = _FakeClient(fake)
    out = build_task_contexts(
        client, summary=_summary_with_items(), segments=_segments(), model="claude-sonnet-4-6",
    )
    title = out[("my", 0)].title
    assert title is not None
    assert title.endswith("…")
    assert len(title) <= 81  # 80 chars + ellipsis
    assert not title[:-1].endswith(" ")  # truncated at a word boundary, no trailing space
    assert long_title.startswith(title[:-1])


def test_client_exception_yields_empty_dict() -> None:
    client = _FakeClient(RuntimeError("network down"))
    out = build_task_contexts(
        client, summary=_summary_with_items(), segments=_segments(), model="claude-sonnet-4-6",
    )
    assert out == {}


def test_with_options_failure_yields_empty_dict() -> None:
    """Even a client that blows up applying the timeout/retry options
    (not just the .create() call itself) must fail soft."""
    client = _FakeClient(_FakeResp(content=[]), raise_on_with_options=True)
    out = build_task_contexts(
        client, summary=_summary_with_items(), segments=_segments(), model="claude-sonnet-4-6",
    )
    assert out == {}


def test_missing_tool_use_block_yields_empty_dict() -> None:
    client = _FakeClient(_FakeResp(content=[]))
    out = build_task_contexts(
        client, summary=_summary_with_items(), segments=_segments(), model="claude-sonnet-4-6",
    )
    assert out == {}


def test_no_items_skips_the_call_entirely() -> None:
    client = _FakeClient(_FakeResp(content=[]))
    out = build_task_contexts(
        client, summary=_summary(), segments=_segments(), model="claude-sonnet-4-6",
    )
    assert out == {}
    assert client.messages.calls == []


def test_json_string_tool_input_is_parsed() -> None:
    import json

    fake = _FakeResp(content=[
        _FakeBlock(
            name="save_task_contexts",
            input_=json.dumps({
                "contexts": [{"kind": "my", "index": 0, "context": "Valid via JSON string."}],
            }),
        ),
    ])
    client = _FakeClient(fake)
    out = build_task_contexts(
        client, summary=_summary_with_items(), segments=_segments(), model="claude-sonnet-4-6",
    )
    assert out == {("my", 0): TaskCopy(title=None, context="Valid via JSON string.")}


def test_transcript_is_truncated_to_char_budget() -> None:
    long_segments = [
        TranscriptSegment(id=None, recording_id=1, start_ms=i * 1000, end_ms=i * 1000 + 500,
                          channel=Channel.ME, text="word " * 20)
        for i in range(50)
    ]
    fake = _FakeResp(content=[
        _FakeBlock(name="save_task_contexts", input_={"contexts": []}),
    ])
    client = _FakeClient(fake)
    build_task_contexts(
        client, summary=_summary_with_items(), segments=long_segments,
        model="claude-sonnet-4-6", char_budget=200,
    )
    sent_text = client.messages.calls[0]["messages"][0]["content"]
    # The transcript block should be capped near the budget, not the full text.
    assert len(sent_text) < 2000
