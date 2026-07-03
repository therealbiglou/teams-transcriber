"""WrikeSyncPlanner: rows, gated format dropdowns, lock-when-synced, footer counts."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QPushButton

from teams_transcriber.integrations.wrike_items import SyncItem
from teams_transcriber.integrations.wrike_assignees import Contact
from teams_transcriber.ui.wrike_sync_planner import WrikeSyncPlanner


def _items() -> list[SyncItem]:
    return [
        SyncItem(kind="summary", index=0, text="we aligned"),
        SyncItem(kind="decisions", index=0, text="- Ship in July"),
        SyncItem(kind="my_todo", index=0, text="Email Jennifer"),
        SyncItem(kind="action_other", index=0, text="Migration doc",
                 suggested_who="Sarah Kim"),
        SyncItem(kind="follow_up", index=0, text="Revisit pricing"),
    ]


def _folders() -> list[dict]:
    return [{"id": "F1", "title": "Project A"}, {"id": "F2", "title": "Project B"}]


def _contacts() -> list[Contact]:
    return [
        Contact(id="100", first_name="Jennifer", last_name="Smith"),
        Contact(id="200", first_name="Sarah", last_name="Kim"),
    ]


def test_planner_renders_one_row_per_item(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=_items(),
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={3: "200"},
        already_synced_keys=set(),
    )
    row_cbs = dlg.findChildren(QCheckBox)
    enabled_cbs = [cb for cb in row_cbs if cb.objectName() == "row-include"]
    assert len(enabled_cbs) == 5
    assert all(cb.isChecked() for cb in enabled_cbs)


def test_planner_format_dropdown_is_kind_gated(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=_items(),
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={},
        already_synced_keys=set(),
    )
    combos = [c for c in dlg.findChildren(QComboBox) if c.objectName() == "row-format"]
    assert len(combos) == 5
    options_by_kind = {item.kind: [combos[i].itemText(j) for j in range(combos[i].count())]
                       for i, item in enumerate(_items())}
    assert set(options_by_kind["summary"]) == {"Comment", "Task"}
    assert set(options_by_kind["decisions"]) == {"Comment", "Task"}
    assert options_by_kind["my_todo"] == ["Task"]
    assert options_by_kind["action_other"] == ["Task"]
    assert set(options_by_kind["follow_up"]) == {"Task", "Comment"}


def test_planner_locks_synced_rows(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=_items(),
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={},
        already_synced_keys={("my_todo", 0)},
    )
    cbs = [cb for cb in dlg.findChildren(QCheckBox) if cb.objectName() == "row-include"]
    locked_cb = cbs[2]
    assert locked_cb.isChecked()
    assert not locked_cb.isEnabled()
    send_btn = next(b for b in dlg.findChildren(QPushButton) if b.objectName() == "send-btn")
    assert "Send 4" in send_btn.text()


def test_planner_footer_count_updates_on_uncheck(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=_items(),
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={},
        already_synced_keys=set(),
    )
    send_btn = next(b for b in dlg.findChildren(QPushButton) if b.objectName() == "send-btn")
    assert "Send 5" in send_btn.text()
    cbs = [cb for cb in dlg.findChildren(QCheckBox) if cb.objectName() == "row-include"]
    cbs[0].setChecked(False)
    assert "Send 4" in send_btn.text()


def test_planner_build_plan_returns_only_checked_unlocked(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=_items(),
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={3: "200"},
        already_synced_keys={("decisions", 0)},
    )
    cbs = [cb for cb in dlg.findChildren(QCheckBox) if cb.objectName() == "row-include"]
    cbs[3].setChecked(False)
    plan = dlg.build_plan()
    kinds = [r.item.kind for r in plan]
    assert kinds == ["summary", "my_todo", "follow_up"]
    assert [r.format for r in plan] == ["comment", "task", "task"]
    assert all(r.folder_id == "F1" for r in plan)


def test_planner_send_disabled_when_no_default_folder(qapp) -> None:
    dlg = WrikeSyncPlanner(
        items=[_items()[0]],
        folders=[],
        recent_folder_ids=[],
        contacts=[],
        assignee_suggestions={},
        already_synced_keys=set(),
    )
    send_btn = next(b for b in dlg.findChildren(QPushButton) if b.objectName() == "send-btn")
    assert not send_btn.isEnabled()


def test_planner_preview_tooltip_has_full_text(qapp) -> None:
    from PySide6.QtWidgets import QLabel
    long_item_text = (
        "This is a very long item text that will certainly be truncated by "
        "the preview function because it exceeds the eighty character limit."
    )
    items = [SyncItem(kind="summary", index=0, text=long_item_text)]
    dlg = WrikeSyncPlanner(
        items=items,
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={},
        already_synced_keys=set(),
    )
    preview = next(
        lbl for lbl in dlg.findChildren(QLabel) if lbl.wordWrap() and lbl.text() != ""
    )
    assert preview.toolTip() == long_item_text


def test_planner_suggested_assignee_flows_into_plan(qapp) -> None:
    # items[3] is the action_other row; suggestion maps it to contact 200.
    dlg = WrikeSyncPlanner(
        items=_items(),
        folders=_folders(),
        recent_folder_ids=["F1"],
        contacts=_contacts(),
        assignee_suggestions={3: "200"},
        already_synced_keys=set(),
    )
    plan = dlg.build_plan()
    action = next(r for r in plan if r.item.kind == "action_other")
    assert action.assignee_id == "200"
    # Non-action_other rows never carry an assignee.
    assert all(r.assignee_id is None for r in plan if r.item.kind != "action_other")
