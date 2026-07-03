def test_should_offer_wrike_sync_predicate():
    from teams_transcriber.ui.app import _wrike_should_offer_sync
    assert _wrike_should_offer_sync(enabled=False, has_token=True, already_synced=False) is False
    assert _wrike_should_offer_sync(enabled=True, has_token=False, already_synced=False) is False
    assert _wrike_should_offer_sync(enabled=True, has_token=True, already_synced=True) is False
    assert _wrike_should_offer_sync(enabled=True, has_token=True, already_synced=False) is True


def test_lru_recent_folder_ids():
    from teams_transcriber.ui.app import _wrike_lru_push
    assert _wrike_lru_push([], "F1", cap=5) == ["F1"]
    assert _wrike_lru_push(["A", "B", "C"], "B", cap=5) == ["B", "A", "C"]
    assert _wrike_lru_push(["A", "B", "C", "D", "E"], "F", cap=5) == ["F", "A", "B", "C", "D"]
