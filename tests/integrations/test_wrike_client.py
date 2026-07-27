import json

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
        # Correct Wrike v4 form: GET /contacts?me=true (NOT /contacts/me — that
        # path interprets "me" as a contact id and 400s with
        # "invalid ContactOrInvitation ID").
        assert req.url.path.endswith("/contacts")
        assert req.url.params.get("me") == "true"
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


def test_list_spaces():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v4/spaces"
        return httpx.Response(200, json={"data": [{"id": "sp1", "title": "Team"}]})
    client = _client(handler)
    assert client.list_spaces() == [{"id": "sp1", "title": "Team"}]


def test_get_folder_returns_single_dict_with_child_ids():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v4/folders/sp1"
        return httpx.Response(
            200,
            json={"data": [{"id": "sp1", "title": "Team", "childIds": ["f1", "f2"]}]},
        )
    client = _client(handler)
    out = client.get_folder("sp1")
    assert out == {"id": "sp1", "title": "Team", "childIds": ["f1", "f2"]}


def test_get_folder_returns_empty_dict_when_no_data():
    def handler(request): return httpx.Response(200, json={"data": []})
    client = _client(handler)
    assert client.get_folder("missing") == {}


def test_create_project_sets_project_field():
    seen = {}
    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"id": "prj1", "permalink": "https://wrike/open.htm?id=1"}]})
    client = _client(handler)
    out = client.create_project("parent1", "Q3 sync", "the description")
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v4/folders/parent1/folders"
    assert seen["body"]["title"] == "Q3 sync"
    assert seen["body"]["description"] == "the description"
    assert "project" in seen["body"]           # the field that makes it a project
    assert out["id"] == "prj1" and out["permalink"].endswith("id=1")


def test_list_custom_item_types():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v4/custom_item_types"
        return httpx.Response(200, json={"data": [
            {"id": "IEAGW7W6PIAJCFTL", "title": "Meeting", "relatedType": "Project"},
        ]})
    client = _client(handler)
    out = client.list_custom_item_types()
    assert out[0]["id"] == "IEAGW7W6PIAJCFTL"
    assert out[0]["title"] == "Meeting"


def test_create_project_includes_custom_item_type_id_when_supplied():
    seen = {}
    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"id": "prj1", "permalink": "https://wrike/open.htm?id=1"}]})
    client = _client(handler)
    client.create_project("parent1", "Q3 sync", "desc", custom_item_type_id="IEAGW7W6PIAJCFTL")
    assert seen["body"]["customItemTypeId"] == "IEAGW7W6PIAJCFTL"


def test_create_project_omits_custom_item_type_id_when_not_supplied():
    seen = {}
    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"id": "prj1", "permalink": "https://wrike/open.htm?id=1"}]})
    client = _client(handler)
    client.create_project("parent1", "Q3 sync", "desc")
    assert "customItemTypeId" not in seen["body"]
    assert seen["body"] == {"title": "Q3 sync", "description": "desc", "project": {}}


def test_create_project_omits_custom_item_type_id_when_empty_string():
    seen = {}
    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"id": "prj1"}]})
    client = _client(handler)
    client.create_project("parent1", "Q3 sync", "desc", custom_item_type_id="")
    assert "customItemTypeId" not in seen["body"]


def test_update_project_description():
    seen = {}
    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"id": "prj1"}]})
    client = _client(handler)
    client.update_project("prj1", description="new desc")
    assert seen["method"] == "PUT" and seen["path"] == "/api/v4/folders/prj1"
    assert seen["body"] == {"description": "new desc"}


def test_upload_attachment_sends_raw_body_and_filename_header():
    seen = {}
    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["name"] = request.headers.get("X-File-Name")
        seen["content"] = request.content
        return httpx.Response(200, json={"data": [{"id": "att9"}]})
    client = _client(handler)
    att_id = client.upload_attachment("prj1", "transcript.md", b"# hi\nbody")
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v4/folders/prj1/attachments"
    assert seen["name"] == "transcript.md"
    assert seen["content"] == b"# hi\nbody"
    assert att_id == "att9"


def test_delete_attachment():
    seen = {}
    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"data": []})
    client = _client(handler)
    client.delete_attachment("att9")
    assert seen["method"] == "DELETE" and seen["path"] == "/api/v4/attachments/att9"


def test_upload_attachment_retries_on_429_then_succeeds():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"errorDescription": "throttled"})
        return httpx.Response(200, json={"data": [{"id": "att9"}]})
    client = _client(handler)
    att_id = client.upload_attachment("prj1", "transcript.md", b"body")
    assert calls["n"] == 3 and att_id == "att9"


def test_upload_attachment_gives_up_with_rate_limit_error():
    def handler(request): return httpx.Response(429, headers={"Retry-After": "0"})
    client = _client(handler)
    with pytest.raises(WrikeRateLimitError):
        client.upload_attachment("prj1", "transcript.md", b"body")
