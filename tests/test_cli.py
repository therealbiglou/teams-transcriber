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


def test_phone_sync_command_runs_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    """`phone-sync <folder>` runs one full cycle: pull the outbox item, import
    it, wait for post-processing, and print the report. import_phone_recording
    is monkeypatched to create a real DONE Recording row directly (no Whisper/
    Claude involved), matching how tests/phone_sync/test_sync.py fakes it."""
    import json

    from teams_transcriber.pipeline import Pipeline
    from teams_transcriber.storage import (
        Recording,
        RecordingRepo,
        RecordingSource,
        RecordingStatus,
    )

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    phone_dir = tmp_path / "phone"
    outbox = phone_dir / "outbox"
    outbox.mkdir(parents=True)
    (outbox / "rec_uid-1.m4a").write_bytes(b"fake-audio-bytes")
    (outbox / "rec_uid-1.json").write_text(json.dumps({
        "uid": "uid-1", "title": "Standup", "source": "memo",
        "started_at": "2026-07-14T10:00:00+00:00",
    }))

    created_ids: list[int] = []

    def fake_import_phone_recording(self, src_path, *, title, started_at):
        rec = RecordingRepo(self.db).create(Recording(
            id=None,
            started_at=(started_at.isoformat() if started_at else "2026-07-14T10:00:00+00:00"),
            ended_at=None,
            source=RecordingSource.MANUAL,
            detected_title=title or "Untitled",
            display_title=title or "Untitled",
            audio_path=None, audio_deleted_at=None, duration_ms=1000,
            status=RecordingStatus.DONE, error_message=None,
        ))
        assert rec.id is not None
        created_ids.append(rec.id)
        return rec.id

    monkeypatch.setattr(Pipeline, "import_phone_recording", fake_import_phone_recording)

    rc = main(["phone-sync", str(phone_dir)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Imported 1" in out
    assert created_ids == [1]
    assert not (outbox / "rec_uid-1.m4a").exists()
    assert not (outbox / "rec_uid-1.json").exists()
    assert (phone_dir / "library" / "manifest.json").is_file()
