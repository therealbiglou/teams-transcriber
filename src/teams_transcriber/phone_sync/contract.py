"""The phone↔desktop sync contract: JSON shapes exchanged via the phone's
Documents/TeamsTranscriber/ folder. Single source of truth for schema_version.

Spec: docs/superpowers/specs/2026-07-14-android-companion-design.md.
Parsing is strict for sidecars (a bad sidecar fails that one import) and
tolerant for changes.json (one malformed toggle never blocks the rest).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

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
    return Sidecar(
        uid=str(data["uid"]),
        title=str(data["title"]),
        source=str(data["source"]),
        started_at=str(data["started_at"]),
        ended_at=(str(data["ended_at"]) if data.get("ended_at") else None),
        duration_ms=(int(data["duration_ms"]) if data.get("duration_ms") is not None else None),
        app_version=str(data.get("app_version", "")),
    )


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
            out.append(TodoChange(
                recording_id=int(entry["recording_id"]),
                todo_index=int(entry["todo_index"]),
                done=bool(entry["done"]),
                toggled_at=str(entry["toggled_at"]),
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
