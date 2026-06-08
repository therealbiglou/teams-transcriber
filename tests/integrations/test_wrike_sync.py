import pytest

from teams_transcriber.paths import AppPaths
from teams_transcriber.storage import build_database
from teams_transcriber.storage.models import (
    ActionItemOther, Recording, RecordingSource, RecordingStatus, Summary, TodoItem,
)
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.summaries import SummaryRepo
from teams_transcriber.storage.wrike import WrikeTaskRepo
from teams_transcriber.integrations.wrike_sync import sync_recording, SyncResult


class _FakeClient:
    def __init__(self, contacts=None) -> None:
        self.contacts = contacts or []
        self.created: list[tuple[str, dict]] = []
        self._next = 1

    def test_connection(self): return {"id": "SELF"}
    def list_contacts(self): return self.contacts
    def create_task(self, folder_id, payload):
        tid = f"T{self._next}"; self._next += 1
        self.created.append((folder_id, payload))
        return {"id": tid}
    def complete_task(self, task_id, *, done): pass


@pytest.fixture
def env(tmp_path):
    paths = AppPaths(root=tmp_path); paths.ensure_dirs()
    db = build_database(paths.db_path); db.initialize()
    yield paths, db
    db.close()


def _make_recording_with_summary(db, my_todos, others) -> int:
    rec = RecordingRepo(db).create(Recording(
        id=None, started_at="2026-06-07T10:00:00+00:00", ended_at=None,
        source=RecordingSource.MANUAL, detected_title="Potter Sync",
        display_title="Potter Sync", audio_path=None, audio_deleted_at=None,
        duration_ms=1500, status=RecordingStatus.DONE, error_message=None,
    ))
    SummaryRepo(db).upsert(Summary(
        recording_id=rec.id, title="Potter Sync", one_line=None,
        summary="ok", key_decisions=[], my_todos=my_todos,
        action_items_others=others, follow_ups=[], topics=[],
        generated_at="2026-06-07T11:00:00+00:00", model_used="m",
    ))
    return rec.id


def test_sync_creates_tasks_for_my_and_others_and_persists_mappings(env):
    _, db = env
    rid = _make_recording_with_summary(
        db,
        my_todos=[TodoItem(task="Email Jennifer", due="2026-06-09"),
                  TodoItem(task="Order banner")],
        others=[ActionItemOther(who="Jennifer Smith", task="Send floor plan")],
    )
    client = _FakeClient(contacts=[
        {"id": "C_JEN", "firstName": "Jennifer", "lastName": "Smith"},
    ])
    res: SyncResult = sync_recording(db, client, rid, folder_id="F1")

    assert res.created_my == 2 and res.created_other == 1 and res.assigned_other == 1
    assert all(folder == "F1" for folder, _ in client.created)
    assert len(client.created) == 3
    my_payloads = [p for (_, p) in client.created if p["title"] in ("Email Jennifer", "Order banner")]
    assert my_payloads[0]["responsibles"] == ["SELF"]
    assert any(p.get("dates", {}).get("due") == "2026-06-09" for p in my_payloads)
    other_payload = next(p for (_, p) in client.created if p["title"].startswith("For Jennifer Smith"))
    assert other_payload["responsibles"] == ["C_JEN"]
    rows = WrikeTaskRepo(db).list_for_recording(rid)
    assert {r.kind for r in rows} == {"my", "other"}
    assert len(rows) == 3


def test_sync_is_idempotent_for_already_mapped_todos(env):
    _, db = env
    rid = _make_recording_with_summary(
        db, my_todos=[TodoItem(task="A")], others=[],
    )
    client = _FakeClient()
    sync_recording(db, client, rid, folder_id="F1")
    sync_recording(db, client, rid, folder_id="F1")
    assert len(client.created) == 1
    assert len(WrikeTaskRepo(db).list_for_recording(rid)) == 1


def test_sync_unassigns_when_contact_match_is_missing(env):
    _, db = env
    rid = _make_recording_with_summary(
        db, my_todos=[], others=[ActionItemOther(who="Unknown Person", task="X")],
    )
    client = _FakeClient(contacts=[
        {"id": "C_JEN", "firstName": "Jennifer", "lastName": "Smith"},
    ])
    res = sync_recording(db, client, rid, folder_id="F1")
    assert res.created_other == 1 and res.assigned_other == 0
    payload = client.created[0][1]
    assert "responsibles" not in payload or payload["responsibles"] == []


def test_sync_case_insensitive_exact_match_for_others(env):
    _, db = env
    rid = _make_recording_with_summary(
        db, my_todos=[], others=[ActionItemOther(who="jennifer smith", task="X")],
    )
    client = _FakeClient(contacts=[
        {"id": "C_JEN", "firstName": "Jennifer", "lastName": "Smith"},
    ])
    res = sync_recording(db, client, rid, folder_id="F1")
    assert res.assigned_other == 1
    assert client.created[0][1]["responsibles"] == ["C_JEN"]


def test_sync_ambiguous_match_does_not_assign(env):
    _, db = env
    rid = _make_recording_with_summary(
        db, my_todos=[], others=[ActionItemOther(who="John Doe", task="X")],
    )
    client = _FakeClient(contacts=[
        {"id": "C_JOHN1", "firstName": "John", "lastName": "Doe"},
        {"id": "C_JOHN2", "firstName": "John", "lastName": "Doe"},   # duplicate full name
    ])
    res = sync_recording(db, client, rid, folder_id="F1")
    assert res.assigned_other == 0


# --- date validation ---------------------------------------------------

def test_safe_iso_date_accepts_yyyy_mm_dd():
    from teams_transcriber.integrations.wrike_sync import _safe_iso_date
    assert _safe_iso_date("2026-06-15") == "2026-06-15"


def test_safe_iso_date_normalizes_full_iso_datetime():
    from teams_transcriber.integrations.wrike_sync import _safe_iso_date
    # Wrike's dates.due wants a date; strip the time component.
    assert _safe_iso_date("2026-06-15T10:30:00") == "2026-06-15"


def test_safe_iso_date_rejects_freeform_strings():
    from teams_transcriber.integrations.wrike_sync import _safe_iso_date
    for v in (None, "", "  ", "next Tuesday", "by EOD Friday",
              "Q3", "TBD", "ASAP", "6/15", "June 15", "2026-06"):
        assert _safe_iso_date(v) is None, f"should reject {v!r}"


def test_sync_skips_dates_and_appends_to_description_when_due_is_freeform(env):
    _, db = env
    rid = _make_recording_with_summary(
        db,
        my_todos=[TodoItem(task="Email Jennifer", due="next Tuesday")],
        others=[],
    )
    client = _FakeClient()
    res = sync_recording(db, client, rid, folder_id="F1")
    assert res.created_my == 1
    payload = client.created[0][1]
    # Unparseable due is NOT sent as `dates.*` (Wrike would 400).
    assert "dates" not in payload
    # ...but the info is preserved in the description so the user still sees it.
    assert "Due (as written): next Tuesday" in payload["description"]


def test_sync_sends_dates_when_due_is_iso(env):
    _, db = env
    rid = _make_recording_with_summary(
        db,
        my_todos=[TodoItem(task="Email Jennifer", due="2026-06-15")],
        others=[],
    )
    client = _FakeClient()
    sync_recording(db, client, rid, folder_id="F1")
    payload = client.created[0][1]
    assert payload["dates"] == {"due": "2026-06-15"}
    assert "Due (as written)" not in payload["description"]


def test_sync_same_behavior_for_action_items_others(env):
    _, db = env
    rid = _make_recording_with_summary(
        db,
        my_todos=[],
        others=[ActionItemOther(who="Jennifer Smith", task="Send floor plan",
                                due="end of week")],
    )
    client = _FakeClient()
    sync_recording(db, client, rid, folder_id="F1")
    payload = client.created[0][1]
    assert "dates" not in payload
    assert "Due (as written): end of week" in payload["description"]
