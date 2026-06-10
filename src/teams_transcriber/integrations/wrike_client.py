"""Wrike REST API client.

Permanent Access Token auth. Stateless: instantiate with a token + optional
custom transport (used by tests). All methods raise typed exceptions on
HTTP failure; the 429 path backs off with two retries before giving up.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

WRIKE_BASE_URL = "https://www.wrike.com/api/v4"
_MAX_RETRIES_ON_429 = 2
_DEFAULT_TIMEOUT_S = 30.0


class WrikeApiError(RuntimeError):
    """Generic Wrike API failure (non-auth, non-rate-limit)."""


class WrikeAuthError(WrikeApiError):
    """401/403 — token missing or invalid."""


class WrikeRateLimitError(WrikeApiError):
    """429 — exceeded retry budget."""


class WrikeClient:
    def __init__(
        self,
        *,
        token: str,
        base_url: str = WRIKE_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._token = token
        self._client = httpx.Client(
            base_url=base_url, transport=transport, timeout=timeout_s,
            headers={"Authorization": f"bearer {token}"},
        )

    def test_connection(self) -> dict[str, Any]:
        """Return the current user via /contacts?me=true.

        Wrike's `/contacts/{id}` path expects a real contact id; there is no
        `/contacts/me` shorthand. The current user is fetched by filtering the
        list endpoint with `me=true`.
        """
        data = self._request("GET", "/contacts", params={"me": "true"})
        return data[0] if data else {}

    def list_folders(self) -> list[dict[str, Any]]:
        return self._request("GET", "/folders")

    def list_contacts(self) -> list[dict[str, Any]]:
        return self._request("GET", "/contacts")

    def create_task(self, folder_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", f"/folders/{folder_id}/tasks", json=payload)
        return data[0] if data else {}

    def create_comment(
        self,
        *,
        entity_type: str,  # "folder" | "task"
        entity_id: str,
        text: str,
    ) -> str:
        """POST /folders/{id}/comments or /tasks/{id}/comments. Returns the comment id."""
        if entity_type not in ("folder", "task"):
            raise ValueError(
                f"entity_type must be 'folder' or 'task', got {entity_type!r}"
            )
        path = f"/{entity_type}s/{entity_id}/comments"
        data = self._request("POST", path, json={"text": text})
        return str(data[0]["id"]) if data else ""

    def complete_task(self, task_id: str, *, done: bool) -> dict[str, Any]:
        status = "Completed" if done else "Active"
        data = self._request("PUT", f"/tasks/{task_id}", json={"status": status})
        return data[0] if data else {}

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        attempts = 0
        while True:
            attempts += 1
            resp = self._client.request(method, path, json=json, params=params)
            if resp.status_code == 429:
                if attempts > _MAX_RETRIES_ON_429:
                    raise WrikeRateLimitError(
                        f"Wrike rate-limited after {_MAX_RETRIES_ON_429} retries"
                    )
                retry_after = float(resp.headers.get("Retry-After", "1"))
                logger.warning("Wrike 429; backing off %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code in (401, 403):
                detail = (
                    resp.json().get("errorDescription")
                    if resp.headers.get("content-type", "").startswith("application/json")
                    else resp.text
                )
                raise WrikeAuthError(
                    f"Wrike auth failed ({resp.status_code}): {detail}"
                )
            if 500 <= resp.status_code < 600 or not resp.is_success:
                raise WrikeApiError(
                    f"Wrike {method} {path} -> {resp.status_code}: {resp.text[:200]}"
                )
            body = resp.json()
            data = body.get("data")
            return data if isinstance(data, list) else []
