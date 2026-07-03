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


def test_update_dialog_has_shared_chrome(tmp_path, qapp) -> None:
    """UpdateDialog uses the shared frameless chrome with a close button."""
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.ui.frameless import FramelessWindowMixin
    from teams_transcriber.ui.update_dialog import UpdateDialog

    paths = AppPaths(root=tmp_path)
    paths.ensure_dirs()
    dlg = UpdateDialog(
        version="v0.5.1",
        download_url="https://example.com/installer.exe",
        paths=paths,
    )
    assert isinstance(dlg, FramelessWindowMixin)
    assert dlg._title_bar.close_btn is not None


def test_restart_uses_quit_callback_not_sys_exit(qapp, tmp_path, monkeypatch):
    import subprocess

    from teams_transcriber.paths import AppPaths
    from teams_transcriber.ui.update_dialog import UpdateDialog

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: None)
    quits: list[bool] = []
    dlg = UpdateDialog(
        version="v9.9.9", download_url="http://localhost/x.exe",
        paths=AppPaths(root=tmp_path / "TT"),
        quit_callback=lambda: quits.append(True),
    )
    dlg._launch_installer_and_quit()
    assert quits == [True]
