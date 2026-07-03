"""Tests for the GitHub-based update checker."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_parse_version_basic() -> None:
    from teams_transcriber.update_checker import parse_version
    assert parse_version("v0.5.1") == (0, 5, 1)
    assert parse_version("0.5.1") == (0, 5, 1)
    assert parse_version("v0.5.1-rc1") == (0, 5, 1)
    assert parse_version("v10.20.30") == (10, 20, 30)


def test_parse_version_invalid_raises() -> None:
    from teams_transcriber.update_checker import UpdateCheckError, parse_version
    with pytest.raises(UpdateCheckError):
        parse_version("not-a-version")
    with pytest.raises(UpdateCheckError):
        parse_version("v0.1")


def test_is_update_available_newer() -> None:
    from teams_transcriber.update_checker import ReleaseInfo, is_update_available
    latest = ReleaseInfo(
        tag="v0.5.1", version=(0, 5, 1), is_prerelease=False,
        installer_url="", installer_size=0, html_url="",
    )
    assert is_update_available("0.5.0", latest) is True
    assert is_update_available("0.4.9", latest) is True
    assert is_update_available("0.5.1", latest) is False
    assert is_update_available("0.5.2", latest) is False


def test_is_update_available_with_v_prefix_on_installed() -> None:
    from teams_transcriber.update_checker import ReleaseInfo, is_update_available
    latest = ReleaseInfo(
        tag="v0.5.1", version=(0, 5, 1), is_prerelease=False,
        installer_url="", installer_size=0, html_url="",
    )
    assert is_update_available("v0.5.0", latest) is True


def test_is_update_available_with_unparseable_installed() -> None:
    """Treat unparseable installed version as no-update (safe)."""
    from teams_transcriber.update_checker import ReleaseInfo, is_update_available
    latest = ReleaseInfo(
        tag="v0.5.1", version=(0, 5, 1), is_prerelease=False,
        installer_url="", installer_size=0, html_url="",
    )
    assert is_update_available("unknown", latest) is False


def test_fetch_latest_release_picks_exe_asset(monkeypatch) -> None:
    from teams_transcriber import update_checker

    fake_release = [{
        "tag_name": "v0.5.1-rc1",
        "prerelease": True,
        "html_url": "https://github.com/therealbiglou/teams-transcriber/releases/tag/v0.5.1-rc1",
        "assets": [
            {"name": "source.tar.gz", "browser_download_url": "https://example.com/x.tar.gz", "size": 100},
            {"name": "TeamsTranscriberSetup-0.5.1.exe",
             "browser_download_url": "https://example.com/installer.exe",
             "size": 1024 * 1024 * 96},
        ],
    }]

    monkeypatch.setattr(update_checker, "_fetch_json", lambda url: fake_release)
    info = update_checker.fetch_latest_release()
    assert info.tag == "v0.5.1-rc1"
    assert info.version == (0, 5, 1)
    assert info.is_prerelease is True
    assert info.installer_url == "https://example.com/installer.exe"
    assert info.installer_size == 1024 * 1024 * 96


def test_fetch_latest_release_no_exe_asset(monkeypatch) -> None:
    from teams_transcriber import update_checker

    fake_release = [{
        "tag_name": "v0.5.1",
        "prerelease": False,
        "html_url": "",
        "assets": [
            {"name": "source.zip", "browser_download_url": "x", "size": 1},
        ],
    }]
    monkeypatch.setattr(update_checker, "_fetch_json", lambda url: fake_release)
    with pytest.raises(update_checker.UpdateCheckError):
        update_checker.fetch_latest_release()


def test_fetch_latest_release_empty_list(monkeypatch) -> None:
    from teams_transcriber import update_checker
    monkeypatch.setattr(update_checker, "_fetch_json", lambda url: [])
    with pytest.raises(update_checker.UpdateCheckError):
        update_checker.fetch_latest_release()


def test_fetch_latest_release_network_error(monkeypatch) -> None:
    from teams_transcriber import update_checker

    def boom(url):
        raise OSError("no network")

    monkeypatch.setattr(update_checker, "_fetch_json", boom)
    with pytest.raises(update_checker.UpdateCheckError):
        update_checker.fetch_latest_release()


def test_download_installer_writes_file(tmp_path, monkeypatch) -> None:
    from teams_transcriber.update_checker import ReleaseInfo, download_installer

    payload = b"\x00" * 4096
    target = tmp_path / "installer.exe"

    class _FakeResponse:
        def __init__(self):
            self._data = payload
            self._offset = 0
            self.headers = {"Content-Length": str(len(payload))}
        def read(self, n):
            chunk = self._data[self._offset:self._offset + n]
            self._offset += len(chunk)
            return chunk
        def __enter__(self): return self
        def __exit__(self, *a): return None

    import teams_transcriber.update_checker as uc
    monkeypatch.setattr(uc.urllib.request, "urlopen", lambda req, timeout=60: _FakeResponse())

    release = ReleaseInfo(
        tag="v0.5.1", version=(0, 5, 1), is_prerelease=False,
        installer_url="https://example.com/installer.exe",
        installer_size=len(payload), html_url="",
    )
    progress_calls: list[tuple[int, int]] = []
    download_installer(
        release, target,
        progress_callback=lambda done, total: progress_calls.append((done, total)),
    )
    assert target.exists()
    assert target.read_bytes() == payload
    assert progress_calls[-1][0] == len(payload)
