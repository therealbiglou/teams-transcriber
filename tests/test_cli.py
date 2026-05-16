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
