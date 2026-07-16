"""The phone↔desktop sync contract: JSON shapes exchanged via the phone's
Documents/TeamsTranscriber/ folder. Single source of truth for schema_version.

Spec: docs/superpowers/specs/2026-07-14-android-companion-design.md.
Parsing is strict for sidecars (a bad sidecar fails that one import) and
tolerant for changes.json (one malformed toggle never blocks the rest).

Timestamps exchanged in the contract (`started_at`, `ended_at`, `toggled_at`,
`changes_applied_through`, `exported_at`) are ISO-8601 UTC with an explicit
`+00:00` offset (never `Z`), so lexicographic comparison equals chronological
comparison.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
VALID_SOURCES = frozenset({"teams_call", "in_person", "memo"})


class ContractError(ValueError):
    """Raised when a phone-written file doesn't match the contract."""


@dataclass(slots=True)
class Sidecar:
    uid: str
    title: str
    source: str
    started_at: str
    ended_at: str | None
    duration_ms: int | None
    app_version: str


@dataclass(slots=True)
class TodoChange:
    recording_id: int
    todo_index: int
    done: bool
    toggled_at: str


def parse_sidecar(text: str) -> Sidecar:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"sidecar is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError("sidecar must be a JSON object")
    missing = [k for k in ("uid", "title", "source", "started_at") if not data.get(k)]
    if missing:
        raise ContractError(f"sidecar missing required fields: {missing}")
    if data["source"] not in VALID_SOURCES:
        raise ContractError(f"sidecar source {data['source']!r} not in {sorted(VALID_SOURCES)}")
    started_at = str(data["started_at"])
    _require_aware_isoformat(started_at, field="sidecar started_at")
    ended_at = str(data["ended_at"]) if data.get("ended_at") else None
    if ended_at is not None:
        _require_aware_isoformat(ended_at, field="sidecar ended_at")
    try:
        duration_ms = (
            int(data["duration_ms"]) if data.get("duration_ms") is not None else None
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(
            f"sidecar duration_ms invalid: {data['duration_ms']!r}"
        ) from exc
    return Sidecar(
        uid=str(data["uid"]),
        title=str(data["title"]),
        source=str(data["source"]),
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=duration_ms,
        app_version=str(data.get("app_version", "")),
    )


def _require_aware_isoformat(value: str, *, field: str) -> None:
    """Reject a timestamp that isn't a timezone-aware ISO-8601 string.

    A naive started_at/ended_at would flow verbatim into
    recordings.started_at, which drives ORDER BY -- and the LWW toggle
    comparison depends on every exchanged timestamp being an unambiguous
    instant (comparing aware vs naive datetimes raises TypeError).
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{field} is not valid ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{field} must be timezone-aware: {value!r}")


def parse_changes(text: str) -> list[TodoChange]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("changes.json is not valid JSON, ignoring: %s", exc)
        return []
    if not isinstance(data, list):
        logger.warning("changes.json is not a list, ignoring")
        return []
    out: list[TodoChange] = []
    for entry in data:
        try:
            if not isinstance(entry["done"], bool):
                raise TypeError(f"done must be a bool, got {type(entry['done']).__name__}")
            toggled_at = str(entry["toggled_at"])
            # A naive toggled_at would raise TypeError deep in the LWW
            # comparison (aware vs naive) -- reject it here so it's skipped
            # like any other malformed entry (ContractError is a ValueError).
            _require_aware_isoformat(toggled_at, field="toggled_at")
            out.append(TodoChange(
                recording_id=int(entry["recording_id"]),
                todo_index=int(entry["todo_index"]),
                done=entry["done"],
                toggled_at=toggled_at,
            ))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("skipping malformed change entry %r: %s", entry, exc)
    return out


def build_ack(imported: list[dict], changes_applied_through: str | None) -> str:
    return json.dumps({
        "schema_version": SCHEMA_VERSION,
        "imported": imported,
        "changes_applied_through": changes_applied_through,
    }, indent=2)


def build_manifest(desktop_version: str, exported_at: str) -> str:
    return json.dumps({
        "schema_version": SCHEMA_VERSION,
        "desktop_version": desktop_version,
        "exported_at": exported_at,
    }, indent=2)
