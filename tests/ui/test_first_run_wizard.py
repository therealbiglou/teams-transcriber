from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.config import load_settings
from teams_transcriber.paths import AppPaths
from teams_transcriber.ui.first_run_wizard import FirstRunWizard


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    p = AppPaths(root=tmp_path / "TT")
    p.ensure_dirs()
    return p


@pytest.fixture(autouse=True)
def _isolate_keyring_and_registry(monkeypatch):
    """Keep wizard tests from touching real keyring and HKCU Run key."""
    monkeypatch.setattr("keyring.set_password", lambda *a, **kw: None)
    monkeypatch.setattr(
        "teams_transcriber.autolaunch.enable", lambda *a, **kw: True,
    )
    monkeypatch.setattr(
        "teams_transcriber.autolaunch.disable", lambda: True,
    )


def test_wizard_has_shared_chrome(qapp, qtbot, paths: AppPaths) -> None:
    from teams_transcriber.ui.frameless import FramelessWindowMixin

    settings = load_settings(paths)
    wizard = FirstRunWizard(
        settings=settings, paths=paths, model_downloader=lambda cb: None,
    )
    assert isinstance(wizard, FramelessWindowMixin)
    assert wizard._title_bar.close_btn is not None


def test_finish_writes_marker_file(qapp, qtbot, paths: AppPaths) -> None:
    settings = load_settings(paths)
    wizard = FirstRunWizard(
        settings=settings, paths=paths, model_downloader=lambda cb: None,
    )
    wizard.auto_launch_cb.setChecked(False)
    wizard._finish()
    assert paths.first_run_marker_path.exists()


def test_finish_persists_api_key_when_provided(
    qapp, qtbot, paths: AppPaths, monkeypatch,
) -> None:
    settings = load_settings(paths)
    saved: dict[str, str] = {}
    monkeypatch.setattr(
        "keyring.set_password",
        lambda svc, usr, pw: saved.setdefault(usr, pw),
    )
    wizard = FirstRunWizard(
        settings=settings, paths=paths, model_downloader=lambda cb: None,
    )
    wizard.api_key_input.setText("sk-test-fake")
    wizard.auto_launch_cb.setChecked(False)
    wizard._finish()
    assert saved.get("anthropic_api_key") == "sk-test-fake"


def test_finish_skips_keyring_when_api_key_blank(
    qapp, qtbot, paths: AppPaths, monkeypatch,
) -> None:
    settings = load_settings(paths)
    calls: list[tuple] = []
    monkeypatch.setattr("keyring.set_password", lambda *a, **kw: calls.append(a))
    wizard = FirstRunWizard(
        settings=settings, paths=paths, model_downloader=lambda cb: None,
    )
    wizard.api_key_input.setText("")
    wizard.auto_launch_cb.setChecked(False)
    wizard._finish()
    assert calls == []


def test_finish_syncs_autolaunch(
    qapp, qtbot, paths: AppPaths, monkeypatch,
) -> None:
    settings = load_settings(paths)
    calls: list[str] = []
    monkeypatch.setattr(
        "teams_transcriber.autolaunch.enable",
        lambda *a, **kw: calls.append("enable") or True,
    )
    monkeypatch.setattr(
        "teams_transcriber.autolaunch.disable",
        lambda: calls.append("disable") or True,
    )
    wizard = FirstRunWizard(
        settings=settings, paths=paths, model_downloader=lambda cb: None,
    )
    wizard.api_key_input.setText("")
    wizard.auto_launch_cb.setChecked(True)
    wizard._finish()
    assert calls == ["enable"]


def test_wizard_skips_gpu_runtime_download_when_already_installed(
    qapp, qtbot, paths, monkeypatch,
) -> None:
    """If is_runtime_installed returns True, the wizard doesn't kick off a download."""
    from teams_transcriber.config import load_settings
    from teams_transcriber.ui.first_run_wizard import FirstRunWizard

    paths.ensure_dirs()
    settings = load_settings(paths)

    download_calls: list[str] = []

    def fake_download(runtime_base, progress_callback=None):
        download_calls.append("invoked")

    monkeypatch.setattr(
        "teams_transcriber.runtime.gpu_runtime.is_runtime_installed",
        lambda _base: True,
    )
    monkeypatch.setattr(
        "teams_transcriber.runtime.gpu_runtime.download_runtime",
        fake_download,
    )

    wiz = FirstRunWizard(
        settings=settings, paths=paths,
        model_downloader=lambda progress: progress(100),
    )
    wiz._next()  # welcome → setup
    wiz._next()  # setup → gpu runtime (should not invoke download_runtime)
    assert download_calls == []


def test_wizard_kicks_off_gpu_runtime_download_when_not_installed(
    qapp, qtbot, paths, monkeypatch,
) -> None:
    from teams_transcriber.config import load_settings
    from teams_transcriber.ui.first_run_wizard import FirstRunWizard

    paths.ensure_dirs()
    settings = load_settings(paths)

    download_calls: list[str] = []

    def fake_download(runtime_base, progress_callback=None):
        download_calls.append("invoked")
        if progress_callback:
            progress_callback("nvidia-cublas-cu12", 0, 100)
            progress_callback("nvidia-cublas-cu12", 100, 100)

    monkeypatch.setattr(
        "teams_transcriber.runtime.gpu_runtime.is_runtime_installed",
        lambda _base: False,
    )
    monkeypatch.setattr(
        "teams_transcriber.runtime.gpu_runtime.download_runtime",
        fake_download,
    )

    wiz = FirstRunWizard(
        settings=settings, paths=paths,
        model_downloader=lambda progress: progress(100),
    )
    wiz._next()  # welcome → setup
    wiz._next()  # setup → gpu runtime → auto-kick download
    assert download_calls == ["invoked"]
