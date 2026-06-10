"""WrikeClient.create_comment posts to /folders/{id}/comments or /tasks/{id}/comments."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from teams_transcriber.integrations.wrike_client import WrikeApiError, WrikeClient


def _transport(handler):
    return httpx.MockTransport(handler)


def test_create_comment_on_folder() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"data": [{"id": "C123"}]})

    client = WrikeClient(token="t", transport=_transport(handler))
    cid = client.create_comment(entity_type="folder", entity_id="F1", text="hello")
    assert cid == "C123"
    assert seen["url"].endswith("/folders/F1/comments")
    assert seen["body"] == {"text": "hello"}
    client.close()


def test_create_comment_on_task() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [{"id": "C9"}]})

    client = WrikeClient(token="t", transport=_transport(handler))
    cid = client.create_comment(entity_type="task", entity_id="T7", text="ok")
    assert cid == "C9"
    assert seen["url"].endswith("/tasks/T7/comments")
    client.close()


def test_create_comment_rejects_bad_entity_type() -> None:
    # The guard must fire before any network call is attempted.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be called for a bad entity_type")

    client = WrikeClient(token="t", transport=_transport(handler))
    with pytest.raises(ValueError):
        client.create_comment(entity_type="project", entity_id="P1", text="x")  # type: ignore[arg-type]
    client.close()


def test_create_comment_raises_on_empty_response() -> None:
    """An empty data envelope means the comment wasn't created — surface it."""
    client = WrikeClient(
        token="t",
        transport=_transport(lambda r: httpx.Response(200, json={"data": []})),
    )
    with pytest.raises(WrikeApiError):
        client.create_comment(entity_type="folder", entity_id="F1", text="x")
    client.close()
