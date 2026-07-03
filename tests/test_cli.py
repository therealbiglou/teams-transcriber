from __future__ import annotations

from pathlib import Path

import pytest

from teams_transcriber.cli import main


def test_cli_help_runs(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "teams-transcriber" in out


def test_cli_list_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                       capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    rc = main(["list"])
    assert rc == 0


def test_cli_smoke_test_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke-test exits 0 once all top-level package imports succeed."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    rc = main(["smoke-test"])
    assert rc == 0


def test_audio_factory_uses_saved_devices(tmp_path, monkeypatch):
    """The pipeline's audio factory must construct from settings, not defaults."""
    from teams_transcriber.cli import _build_pipeline
    from teams_transcriber.paths import AppPaths

    captured: list = []

    def fake_from_settings(settings):
        captured.append(settings)
        return object()

    monkeypatch.setattr(
        "teams_transcriber.audio.source.RealAudioSource.from_settings",
        staticmethod(fake_from_settings),
    )
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    pipeline = _build_pipeline(paths, with_watcher=False)
    pipeline._audio_source_factory()
    assert len(captured) == 1
    assert hasattr(captured[0], "audio_mic_device")   # a real Settings object
    pipeline.shutdown()


def test_ui_app_factory_does_not_use_default_devices_shim():
    import inspect
    import teams_transcriber.ui.app as app_mod
    assert "from_default_devices" not in inspect.getsource(app_mod)
