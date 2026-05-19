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
