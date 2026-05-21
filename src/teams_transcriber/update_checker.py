"""GitHub-based update checker.

Hits the public GitHub Releases API to detect newer versions and downloads
the installer asset. All network errors degrade gracefully (treated as "no
update available").
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_OWNER = "therealbiglou"
REPO_NAME = "teams-transcriber"
RELEASES_URL = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases?per_page=1"
)


class UpdateCheckError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class ReleaseInfo:
    tag: str             # e.g. "v0.5.1-rc1"
    version: tuple[int, ...]
    is_prerelease: bool
    installer_url: str
    installer_size: int  # bytes
    html_url: str        # release page on github.com


_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def parse_version(tag: str) -> tuple[int, ...]:
    """Parse a release tag into a (major, minor, patch) tuple.

    Accepts: "v0.5.1", "0.5.1", "v0.5.1-rc1". The pre-release suffix is
    ignored for comparison purposes — we treat "v0.5.1-rc1" as 0.5.1.

    Raises UpdateCheckError on unparseable input.
    """
    m = _VERSION_RE.match(tag.strip())
    if m is None:
        raise UpdateCheckError(f"could not parse version from tag {tag!r}")
    return tuple(int(g) for g in m.groups())


def is_update_available(installed_version: str, latest: ReleaseInfo) -> bool:
    """True if `latest.version` > parsed `installed_version`."""
    try:
        installed = parse_version(installed_version)
    except UpdateCheckError:
        # If we can't parse our own version, treat as no update (safe).
        return False
    return latest.version > installed


def _fetch_json(url: str) -> object:
    """Helper for tests to monkeypatch."""
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "teams-transcriber-update-checker",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_latest_release() -> ReleaseInfo:
    """Return ReleaseInfo for the most recent release (including pre-releases).

    Raises UpdateCheckError on network / parse / structural failure.
    """
    try:
        data = _fetch_json(RELEASES_URL)
    except Exception as exc:
        raise UpdateCheckError(f"GitHub API request failed: {exc}") from exc

    if not isinstance(data, list) or not data:
        raise UpdateCheckError("no releases returned from GitHub")

    release = data[0]
    tag = release.get("tag_name")
    if not isinstance(tag, str):
        raise UpdateCheckError("release missing tag_name")

    # Find the .exe installer asset.
    assets = release.get("assets") or []
    installer_url = ""
    installer_size = 0
    for asset in assets:
        name = asset.get("name", "")
        if name.lower().endswith(".exe"):
            installer_url = asset.get("browser_download_url", "")
            installer_size = int(asset.get("size", 0))
            break
    if not installer_url:
        raise UpdateCheckError(f"no .exe installer asset on release {tag}")

    return ReleaseInfo(
        tag=tag,
        version=parse_version(tag),
        is_prerelease=bool(release.get("prerelease", False)),
        installer_url=installer_url,
        installer_size=installer_size,
        html_url=release.get("html_url", ""),
    )


def download_installer(
    release: ReleaseInfo,
    target_path: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    """Stream the installer to `target_path`.

    Calls `progress_callback(bytes_done, bytes_total)` periodically.
    Raises UpdateCheckError on any failure. Atomic: writes to a .tmp file
    and renames on success.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")

    req = urllib.request.Request(
        release.installer_url,
        headers={"User-Agent": "teams-transcriber-update-checker"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_path, "wb") as f:
            total = release.installer_size or int(resp.headers.get("Content-Length", 0))
            done = 0
            chunk_size = 64 * 1024
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_callback is not None:
                    progress_callback(done, total)
    except Exception as exc:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise UpdateCheckError(f"download failed: {exc}") from exc

    tmp_path.replace(target_path)
