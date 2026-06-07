import httpx
import pytest

from teams_transcriber.integrations.wrike_client import (
    WrikeApiError, WrikeAuthError, WrikeClient, WrikeRateLimitError,
)


def _client(handler) -> WrikeClient:
    transport = httpx.MockTransport(handler)
    return WrikeClient(token="tok", transport=transport)


def test_test_connection_returns_me_dict():
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/contacts/me")
        assert req.headers["Authorization"] == "bearer tok"
        return httpx.Response(200, json={"data": [{"id": "U1", "firstName": "Brian"}]})
    me = _client(h).test_connection()
    assert me["id"] == "U1"


def test_list_folders_returns_list():
    def h(req): return httpx.Response(200, json={"data": [
        {"id": "F1", "title": "Inbox"}, {"id": "F2", "title": "Meetings"},
    ]})
    out = _client(h).list_folders()
    assert [f["id"] for f in out] == ["F1", "F2"]


def test_list_contacts_returns_list():
    def h(req): return httpx.Response(200, json={"data": [
        {"id": "C1", "firstName": "Jennifer", "lastName": "Smith"},
    ]})
    out = _client(h).list_contacts()
    assert out[0]["firstName"] == "Jennifer"


def test_create_task_posts_to_folder():
    captured = {}
    def h(req):
        captured["url"] = str(req.url)
        captured["body"] = req.read().decode()
        return httpx.Response(200, json={"data": [{"id": "T1"}]})
    out = _client(h).create_task("F1", {"title": "Do thing"})
    assert out["id"] == "T1"
    assert "/folders/F1/tasks" in captured["url"]
    assert '"title":"Do thing"' in captured["body"] or '"title": "Do thing"' in captured["body"]


def test_complete_task_puts_status():
    captured = {}
    def h(req):
        captured["method"] = req.method
        captured["body"] = req.read().decode()
        return httpx.Response(200, json={"data": [{"id": "T1", "status": "Completed"}]})
    _client(h).complete_task("T1", done=True)
    assert captured["method"] == "PUT"
    assert "Completed" in captured["body"]


def test_uncomplete_task_sets_active():
    captured = {}
    def h(req):
        captured["body"] = req.read().decode()
        return httpx.Response(200, json={"data": [{"id": "T1", "status": "Active"}]})
    _client(h).complete_task("T1", done=False)
    assert "Active" in captured["body"]


def test_auth_error_on_401():
    def h(req): return httpx.Response(
        401, json={"errorDescription": "bad token"},
        headers={"content-type": "application/json"},
    )
    with pytest.raises(WrikeAuthError):
        _client(h).list_folders()


def test_rate_limit_retries_then_succeeds():
    calls = {"n": 0}
    def h(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"errorDescription": "throttled"})
        return httpx.Response(200, json={"data": [{"id": "F1", "title": "x"}]})
    out = _client(h).list_folders()
    assert calls["n"] == 2 and out[0]["id"] == "F1"


def test_rate_limit_gives_up_after_two_retries():
    def h(req): return httpx.Response(429, headers={"Retry-After": "0"})
    with pytest.raises(WrikeRateLimitError):
        _client(h).list_folders()


def test_other_5xx_raises_api_error():
    def h(req): return httpx.Response(500, text="boom")
    with pytest.raises(WrikeApiError):
        _client(h).list_folders()
