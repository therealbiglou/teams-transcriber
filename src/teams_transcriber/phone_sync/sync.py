"""One phone-sync cycle: pull outbox -> import -> apply toggles -> export -> ack.

Pure orchestration against the Transport protocol -- no MTP, no UI, no
threads. Callers wire the pipeline import and the Wrike close-loop callback.
Safety rules (spec): idempotent via the phone_imports ledger; a remote file
is deleted only after its import is committed; last-write-wins on toggles;
the desktop never writes changes.json.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from teams_transcriber.phone_sync import contract
from teams_transcriber.phone_sync.library_export import build_library
from teams_transcriber.phone_sync.transport import Transport
from teams_transcriber.storage import Database, PhoneImportRepo, TodoStateRepo

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PhoneSyncReport:
    imported: list[tuple[str, int]] = field(default_factory=list)
    skipped_known: int = 0
    toggles_applied: int = 0
    toggles_skipped_stale: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def _parse_started_at(sidecar: contract.Sidecar) -> datetime | None:
    try:
        return datetime.fromisoformat(sidecar.started_at)
    except ValueError:
        return None


def run_sync(
    db: Database,
    transport: Transport,
    *,
    import_recording: Callable[..., int],
    on_todos_changed: Callable[[int], None] | None = None,
    now_iso: str,
) -> PhoneSyncReport:
    report = PhoneSyncReport()
    ledger = PhoneImportRepo(db)
    ack_entries: list[dict] = []

    remote = {f.name: f for f in transport.list_files("outbox")}
    audio_names = sorted(n for n in remote if n.endswith(".m4a"))

    for audio_name in audio_names:
        sidecar_name = audio_name[: -len(".m4a")] + ".json"
        stem = Path(audio_name).stem
        if sidecar_name not in remote:
            report.failures.append((audio_name, "missing sidecar"))
            ack_entries.append({"uid": stem, "recording_id": None, "result": "missing sidecar"})
            continue
        sidecar_text = transport.read_text(sidecar_name)
        try:
            sidecar = contract.parse_sidecar(sidecar_text or "")
        except contract.ContractError as exc:
            report.failures.append((audio_name, str(exc)))
            ack_entries.append({"uid": stem, "recording_id": None, "result": f"bad sidecar: {exc}"})
            continue
        if ledger.recording_id_for(sidecar.uid) is not None:
            # Already imported in a previous cycle (the ack for it went out
            # then); this is a stale duplicate copy -- just clear it.
            report.skipped_known += 1
            transport.delete(audio_name)
            transport.delete(sidecar_name)
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="tt-phone-") as tmp:
                local = Path(tmp) / Path(audio_name).name
                transport.pull(audio_name, local)
                if local.stat().st_size != remote[audio_name].size:
                    raise OSError(
                        f"size mismatch: got {local.stat().st_size}, "
                        f"expected {remote[audio_name].size}"
                    )
                rid = import_recording(
                    str(local), title=sidecar.title,
                    started_at=_parse_started_at(sidecar),
                )
            ledger.record(sidecar.uid, rid, sidecar.source)
        except Exception as exc:  # one bad file never stops the batch
            logger.exception("phone import failed for %s", audio_name)
            report.failures.append((audio_name, str(exc)))
            ack_entries.append({"uid": sidecar.uid, "recording_id": None, "result": f"failed: {exc}"})
            continue
        transport.delete(audio_name)      # only after ledger commit
        transport.delete(sidecar_name)
        report.imported.append((sidecar.uid, rid))
        ack_entries.append({"uid": sidecar.uid, "recording_id": rid, "result": "imported"})

    # --- todo toggles (desktop never writes changes.json) -------------------
    applied_recordings: set[int] = set()
    max_seen: str | None = None
    changes_text = transport.read_text("outbox/changes.json")
    todo_repo = TodoStateRepo(db)
    for change in contract.parse_changes(changes_text or ""):
        max_seen = max(max_seen or change.toggled_at, change.toggled_at)
        states = {
            s.todo_index: s for s in todo_repo.list_for_recording(change.recording_id)
        }
        current = states.get(change.todo_index)
        if current is None:
            report.failures.append((
                "changes.json",
                f"unknown todo {change.recording_id}/{change.todo_index}",
            ))
            continue
        # ISO-8601 UTC strings (both sides always write datetime.isoformat()
        # in UTC) compare correctly as strings -- no need to parse to datetime.
        if current.done_at is not None and current.done_at >= change.toggled_at:
            report.toggles_skipped_stale += 1
            continue
        todo_repo.mark_done(change.recording_id, change.todo_index, change.done)
        report.toggles_applied += 1
        applied_recordings.add(change.recording_id)

    if on_todos_changed is not None:
        for rid in sorted(applied_recordings):
            on_todos_changed(rid)

    # --- export + ack --------------------------------------------------------
    for name, text in build_library(db, now_iso=now_iso).items():
        transport.push_text(text, name)
    transport.push_text(
        contract.build_ack(ack_entries, changes_applied_through=max_seen),
        "sync/desktop_ack.json",
    )
    return report
