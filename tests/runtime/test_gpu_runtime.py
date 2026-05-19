"""Tests for the GPU runtime download + registration module."""

from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

import pytest


def test_required_packages_pinned() -> None:
    """REQUIRED_PACKAGES is a list of (name, version) tuples, all pinned."""
    from teams_transcriber.runtime.gpu_runtime import REQUIRED_PACKAGES

    assert len(REQUIRED_PACKAGES) > 0
    for entry in REQUIRED_PACKAGES:
        name, version = entry
        assert name.startswith("nvidia-")
        assert version[0].isdigit()
        assert " " not in version
        assert "*" not in version


def test_package_dir_under_runtime_base(tmp_path) -> None:
    from teams_transcriber.runtime.gpu_runtime import package_dir

    d = package_dir(tmp_path, "nvidia-cublas-cu12", "12.4.5.8")
    assert d == tmp_path / "nvidia-cublas-cu12-12.4.5.8"


def test_is_runtime_installed_false_when_dirs_missing(tmp_path) -> None:
    from teams_transcriber.runtime.gpu_runtime import is_runtime_installed

    assert is_runtime_installed(tmp_path) is False


def test_is_runtime_installed_true_when_all_dirs_have_dlls(tmp_path) -> None:
    from teams_transcriber.runtime.gpu_runtime import (
        REQUIRED_PACKAGES,
        is_runtime_installed,
        package_dir,
    )

    for name, version in REQUIRED_PACKAGES:
        bin_dir = package_dir(tmp_path, name, version) / "nvidia" / "x" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "dummy.dll").write_bytes(b"\x00" * 32)

    assert is_runtime_installed(tmp_path) is True


def test_register_runtime_no_op_when_not_installed(tmp_path, monkeypatch) -> None:
    from teams_transcriber.runtime import gpu_runtime

    captured: list[str] = []
    monkeypatch.setattr(os, "add_dll_directory", lambda p: captured.append(p))
    original_path = os.environ.get("PATH", "")

    assert gpu_runtime.register_runtime(tmp_path) is False
    assert captured == []


def test_register_runtime_adds_bin_dirs_when_installed(tmp_path, monkeypatch) -> None:
    from teams_transcriber.runtime import gpu_runtime

    for name, version in gpu_runtime.REQUIRED_PACKAGES:
        bin_dir = gpu_runtime.package_dir(tmp_path, name, version) / "nvidia" / "x" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "fake.dll").write_bytes(b"\x00" * 32)

    captured: list[str] = []
    monkeypatch.setattr(os, "add_dll_directory", lambda p: captured.append(p))
    monkeypatch.setenv("PATH", "")

    assert gpu_runtime.register_runtime(tmp_path) is True
    assert len(captured) == len(gpu_runtime.REQUIRED_PACKAGES)
    new_path = os.environ.get("PATH", "")
    for added in captured:
        assert added in new_path


def test_download_runtime_calls_per_package_with_mocked_fetch(tmp_path, monkeypatch) -> None:
    from teams_transcriber.runtime import gpu_runtime

    def _build_fake_wheel(path: Path, pkg_subdir: str) -> bytes:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(f"nvidia/{pkg_subdir}/bin/fake.dll", b"\x00\x00\x00")
        return path.read_bytes()

    fake_wheels: dict[str, bytes] = {}
    for name, version in gpu_runtime.REQUIRED_PACKAGES:
        wheel_bytes = _build_fake_wheel(
            tmp_path / f"{name}-{version}.whl",
            name.replace("nvidia-", "").replace("-cu12", ""),
        )
        fake_wheels[name] = wheel_bytes

    fetched: list[tuple[str, str]] = []

    def fake_fetch_metadata(name: str, version: str):
        fetched.append((name, version))
        return {
            "url": f"https://example.com/{name}.whl",
            "sha256": hashlib.sha256(fake_wheels[name]).hexdigest(),
        }

    def fake_download(url: str) -> bytes:
        for name in fake_wheels:
            if url.endswith(f"/{name}.whl"):
                return fake_wheels[name]
        raise ValueError(f"unexpected URL {url}")

    monkeypatch.setattr(gpu_runtime, "_fetch_wheel_metadata", fake_fetch_metadata)
    monkeypatch.setattr(gpu_runtime, "_download_bytes", fake_download)

    progress_calls: list[tuple[str, int, int]] = []
    def progress(name: str, done: int, total: int) -> None:
        progress_calls.append((name, done, total))

    gpu_runtime.download_runtime(tmp_path / "runtime", progress_callback=progress)

    assert [n for n, _ in fetched] == [n for n, _ in gpu_runtime.REQUIRED_PACKAGES]
    assert gpu_runtime.is_runtime_installed(tmp_path / "runtime") is True
    seen = {name for name, _, _ in progress_calls}
    assert seen == {name for name, _ in gpu_runtime.REQUIRED_PACKAGES}


def test_download_runtime_sha256_mismatch_raises(tmp_path, monkeypatch) -> None:
    from teams_transcriber.runtime import gpu_runtime

    def fake_metadata(n, v):
        return {"url": "https://example.com/x.whl", "sha256": "0" * 64}

    monkeypatch.setattr(gpu_runtime, "_fetch_wheel_metadata", fake_metadata)
    monkeypatch.setattr(gpu_runtime, "_download_bytes", lambda url: b"corrupt content")

    with pytest.raises(gpu_runtime.GpuRuntimeError):
        gpu_runtime.download_runtime(tmp_path / "runtime")


def test_download_runtime_already_installed_short_circuits(tmp_path, monkeypatch) -> None:
    from teams_transcriber.runtime import gpu_runtime

    for name, version in gpu_runtime.REQUIRED_PACKAGES:
        bin_dir = gpu_runtime.package_dir(tmp_path, name, version) / "nvidia" / "x" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "fake.dll").write_bytes(b"\x00")

    fetched: list[str] = []
    monkeypatch.setattr(
        gpu_runtime, "_fetch_wheel_metadata",
        lambda n, v: fetched.append(n) or {"url": "", "sha256": ""},
    )

    gpu_runtime.download_runtime(tmp_path)
    assert fetched == []
