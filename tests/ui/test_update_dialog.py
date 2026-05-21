"""Smoke test for UpdateDialog construction."""

from __future__ import annotations


def test_update_dialog_constructs(tmp_path, qapp) -> None:
    """UpdateDialog can be constructed without crashing (smoke test)."""
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.ui.update_dialog import UpdateDialog

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    dlg = UpdateDialog(
        version="v0.5.1",
        download_url="https://example.com/installer.exe",
        paths=paths,
    )
    assert dlg.windowTitle() == "Update to v0.5.1"
