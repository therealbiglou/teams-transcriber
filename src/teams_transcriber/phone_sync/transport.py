"""Transport abstraction over the phone's TeamsTranscriber folder.

Names are forward-slash relative paths inside the folder ("outbox/rec_x.m4a").
LocalDirTransport (a plain directory) backs all engine tests and works with
any folder-sync tool; Phase 2 adds MtpTransport for USB.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RemoteFile:
    name: str
    size: int


class Transport(Protocol):
    def list_files(self, prefix: str) -> list[RemoteFile]: ...
    def pull(self, name: str, dest: Path) -> None: ...
    def push(self, src: Path, name: str) -> None: ...
    def push_text(self, text: str, name: str) -> None: ...
    def read_text(self, name: str) -> str | None: ...
    def delete(self, name: str) -> None: ...


class LocalDirTransport:
    def __init__(self, base: Path) -> None:
        self._base = Path(base)

    def _path(self, name: str) -> Path:
        return self._base / Path(name)

    def list_files(self, prefix: str) -> list[RemoteFile]:
        root = self._base / prefix
        if not root.is_dir():
            return []
        out = [
            RemoteFile(
                name=p.relative_to(self._base).as_posix(),
                size=p.stat().st_size,
            )
            for p in root.rglob("*") if p.is_file()
        ]
        return sorted(out, key=lambda f: f.name)

    def pull(self, name: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._path(name), dest)

    def push(self, src: Path, name: str) -> None:
        target = self._path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    def push_text(self, text: str, name: str) -> None:
        target = self._path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def read_text(self, name: str) -> str | None:
        p = self._path(name)
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)
