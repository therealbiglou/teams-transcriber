from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.phone_sync.transport import LocalDirTransport, RemoteFile, validate_name


def test_push_list_pull_delete_roundtrip(tmp_path):
    t = LocalDirTransport(tmp_path / "phone")
    src = tmp_path / "a.m4a"
    src.write_bytes(b"audio-bytes")

    t.push(src, "outbox/rec_a.m4a")
    t.push_text('{"x": 1}', "outbox/rec_a.json")

    files = t.list_files("outbox")
    assert [f.name for f in files] == ["outbox/rec_a.json", "outbox/rec_a.m4a"]
    assert RemoteFile("outbox/rec_a.m4a", len(b"audio-bytes")) in files

    dest = tmp_path / "pulled.m4a"
    t.pull("outbox/rec_a.m4a", dest)
    assert dest.read_bytes() == b"audio-bytes"

    assert t.read_text("outbox/rec_a.json") == '{"x": 1}'
    assert t.read_text("outbox/nope.json") is None

    t.delete("outbox/rec_a.m4a")
    assert [f.name for f in t.list_files("outbox")] == ["outbox/rec_a.json"]


def test_list_files_empty_prefix_dir(tmp_path):
    t = LocalDirTransport(tmp_path / "phone")
    assert t.list_files("outbox") == []


@pytest.mark.parametrize("bad", [
    "../escape.m4a", "outbox/../../x", "C:/evil", "C:\\evil",
    "outbox\\rec.m4a", "/abs/path", "outbox/../x",
])
def test_validate_name_rejects_traversal_and_separators(bad):
    with pytest.raises(ValueError):
        validate_name(bad)


def test_validate_name_accepts_contract_names():
    for good in ("outbox/rec_a.m4a", "library/meetings/5.json", "sync/desktop_ack.json"):
        assert validate_name(good) == good


def test_local_transport_refuses_bad_names(tmp_path):
    from teams_transcriber.phone_sync.transport import LocalDirTransport
    t = LocalDirTransport(tmp_path)
    with pytest.raises(ValueError):
        t.read_text("../outside.txt")
