from __future__ import annotations

import json

import pytest

from teams_transcriber.phone_sync.contract import (
    SCHEMA_VERSION, ContractError, parse_changes, parse_sidecar,
    build_ack, build_manifest,
)


def test_parse_sidecar_roundtrip():
    text = json.dumps({
        "uid": "abc", "title": "Standup", "source": "teams_call",
        "started_at": "2026-07-14T09:00:00+00:00",
        "ended_at": "2026-07-14T09:30:00+00:00",
        "duration_ms": 1_800_000, "app_version": "0.1.0",
    })
    sc = parse_sidecar(text)
    assert (sc.uid, sc.title, sc.source) == ("abc", "Standup", "teams_call")
    assert sc.duration_ms == 1_800_000


def test_parse_sidecar_missing_field_raises():
    with pytest.raises(ContractError):
        parse_sidecar(json.dumps({"uid": "abc", "title": "x", "source": "memo"}))


def test_parse_sidecar_bad_source_raises():
    with pytest.raises(ContractError):
        parse_sidecar(json.dumps({
            "uid": "a", "title": "t", "source": "carrier_pigeon",
            "started_at": "2026-07-14T09:00:00+00:00",
        }))


def test_parse_sidecar_garbage_raises():
    with pytest.raises(ContractError):
        parse_sidecar("not json {")


def test_parse_changes_skips_malformed_entries(caplog):
    text = json.dumps([
        {"recording_id": 3, "todo_index": 0, "done": True,
         "toggled_at": "2026-07-14T10:00:00+00:00"},
        {"recording_id": "oops"},
        {"recording_id": 4, "todo_index": 2, "done": False,
         "toggled_at": "2026-07-14T11:00:00+00:00"},
    ])
    with caplog.at_level("WARNING", logger="teams_transcriber.phone_sync.contract"):
        changes = parse_changes(text)
    assert [(c.recording_id, c.todo_index, c.done) for c in changes] == [
        (3, 0, True), (4, 2, False),
    ]
    assert any("skipping malformed change entry" in rec.message
               for rec in caplog.records)


def test_parse_changes_skips_non_bool_done(caplog):
    text = json.dumps([
        {"recording_id": 5, "todo_index": 1, "done": "false",
         "toggled_at": "2026-07-14T10:00:00+00:00"},
        {"recording_id": 6, "todo_index": 0, "done": True,
         "toggled_at": "2026-07-14T11:00:00+00:00"},
    ])
    with caplog.at_level("WARNING", logger="teams_transcriber.phone_sync.contract"):
        changes = parse_changes(text)
    assert [(c.recording_id, c.todo_index, c.done) for c in changes] == [
        (6, 0, True),
    ]
    assert any("skipping malformed change entry" in rec.message
               for rec in caplog.records)


def test_parse_sidecar_zero_duration_kept():
    sc = parse_sidecar(json.dumps({
        "uid": "a", "title": "t", "source": "memo",
        "started_at": "2026-07-14T09:00:00+00:00",
        "duration_ms": 0,
    }))
    assert sc.duration_ms == 0


def test_parse_sidecar_empty_title_raises():
    with pytest.raises(ContractError):
        parse_sidecar(json.dumps({
            "uid": "a", "title": "", "source": "memo",
            "started_at": "2026-07-14T09:00:00+00:00",
        }))


def test_parse_changes_garbage_returns_empty():
    assert parse_changes("]{") == []


def test_ack_and_manifest_carry_schema_version():
    ack = json.loads(build_ack(
        [{"uid": "a", "recording_id": 1, "result": "imported"}],
        changes_applied_through="2026-07-14T11:00:00+00:00",
    ))
    assert ack["schema_version"] == SCHEMA_VERSION
    assert ack["imported"][0]["uid"] == "a"
    manifest = json.loads(build_manifest("0.10.1", "2026-07-14T12:00:00+00:00"))
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["desktop_version"] == "0.10.1"
