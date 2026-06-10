"""Fuzzy + LLM assignee resolver for action_items_others."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from teams_transcriber.integrations.wrike_assignees import (
    Contact,
    suggest_assignees,
    token_sort_ratio,
)


def _contact(cid: str, first: str, last: str) -> Contact:
    return Contact(id=cid, first_name=first, last_name=last)


# --- Fuzzy matcher unit tests ---

def test_token_sort_ratio_handles_order_swap() -> None:
    assert token_sort_ratio("Jennifer Smith", "Smith Jennifer") == pytest.approx(1.0)


def test_token_sort_ratio_partial_first_name() -> None:
    assert token_sort_ratio("Jen", "Jennifer Smith") > 0.4


def test_token_sort_ratio_zero_on_disjoint() -> None:
    assert token_sort_ratio("Mike Stone", "Sarah Kim") < 0.2


# --- Resolver behaviour ---

class _FakeBlock:
    def __init__(self, name: str, input_: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = input_


class _FakeResp:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessages:
    def __init__(self, scripted: _FakeResp) -> None:
        self._scripted = scripted
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResp:
        self.calls.append(kwargs)
        return self._scripted


class _FakeClient:
    def __init__(self, scripted: _FakeResp) -> None:
        self.messages = _FakeMessages(scripted)


def test_resolver_returns_exact_full_name_match_without_llm() -> None:
    """When fuzzy is confident, the LLM is never called."""
    contacts = [
        _contact("100", "Jennifer", "Smith"),
        _contact("200", "Mike", "Stone"),
    ]
    items = [
        ("idx-0", "Jennifer Smith"),
        ("idx-1", "Mike Stone"),
    ]
    client = _FakeClient(_FakeResp(content=[]))
    out = suggest_assignees(
        items, contacts,
        meeting_summary="—",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=True,
        anthropic_client_factory=lambda _k: client,
    )
    assert out == {"idx-0": "100", "idx-1": "200"}
    assert client.messages.calls == []


def test_resolver_handles_first_name_only() -> None:
    contacts = [_contact("100", "Jennifer", "Smith")]
    items = [("idx-0", "Jen")]
    client = _FakeClient(_FakeResp(content=[]))
    out = suggest_assignees(
        items, contacts,
        meeting_summary="—",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=False,         # fuzzy only
        anthropic_client_factory=lambda _k: client,
    )
    assert out == {"idx-0": "100"}


def test_resolver_returns_none_when_no_confident_match_and_no_llm() -> None:
    contacts = [_contact("100", "Jennifer", "Smith")]
    items = [("idx-0", "the eng lead")]
    client = _FakeClient(_FakeResp(content=[]))
    out = suggest_assignees(
        items, contacts,
        meeting_summary="—",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=False,
        anthropic_client_factory=lambda _k: client,
    )
    assert out == {"idx-0": None}


def test_resolver_falls_back_to_llm_for_unresolved() -> None:
    contacts = [
        _contact("100", "Jennifer", "Smith"),
        _contact("200", "Mike", "Stone"),
    ]
    items = [
        ("idx-0", "Jennifer Smith"),     # fuzzy hit, no LLM needed
        ("idx-1", "the engineering lead"),  # LLM must resolve
        ("idx-2", "someone unknown"),    # LLM returns null
    ]
    fake = _FakeResp(content=[
        _FakeBlock(
            name="resolve_assignees",
            input_={
                "matches": [
                    {"item_index": 1, "contact_id": "200"},
                    {"item_index": 2, "contact_id": None},
                ],
            },
        ),
    ])
    client = _FakeClient(fake)
    out = suggest_assignees(
        items, contacts,
        meeting_summary="Standup with the engineering team",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=True,
        anthropic_client_factory=lambda _k: client,
    )
    assert out == {"idx-0": "100", "idx-1": "200", "idx-2": None}
    assert len(client.messages.calls) == 1


def test_resolver_swallows_llm_failure_returning_null_for_unresolved() -> None:
    """Network/auth failure in the LLM call should not crash; unresolved → None."""
    contacts = [_contact("100", "Jennifer", "Smith")]
    items = [("idx-0", "the eng lead")]

    class _Boom:
        @property
        def messages(self) -> Any:
            raise RuntimeError("network down")

    out = suggest_assignees(
        items, contacts,
        meeting_summary="—",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=True,
        anthropic_client_factory=lambda _k: _Boom(),
    )
    assert out == {"idx-0": None}


def test_resolver_returns_empty_on_empty_items() -> None:
    out = suggest_assignees(
        [], [_contact("100", "A", "B")],
        meeting_summary="—",
        api_key="key", model="claude-haiku-4-5-20251001",
        llm_fallback=True,
        anthropic_client_factory=lambda _k: _FakeClient(_FakeResp([])),
    )
    assert out == {}
