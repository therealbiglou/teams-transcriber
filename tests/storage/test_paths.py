from pathlib import Path

import pytest

from teams_transcriber.paths import AppPaths


def test_paths_defaults_to_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    paths = AppPaths()
    assert paths.root == tmp_path / "TeamsTranscriber"
    assert paths.db_path == tmp_path / "TeamsTranscriber" / "teams_transcriber.db"
    assert paths.audio_dir == tmp_path / "TeamsTranscriber" / "audio"
    assert paths.models_dir == tmp_path / "TeamsTranscriber" / "models"
    assert paths.logs_dir == tmp_path / "TeamsTranscriber" / "logs"
    assert paths.config_dir == tmp_path / "TeamsTranscriber" / "config"


def test_paths_accepts_explicit_root(tmp_path: Path) -> None:
    custom = tmp_path / "custom_root"
    paths = AppPaths(root=custom)
    assert paths.root == custom
    assert paths.db_path == custom / "teams_transcriber.db"


def test_ensure_dirs_creates_all_directories(tmp_path: Path) -> None:
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    assert paths.root.is_dir()
    assert paths.audio_dir.is_dir()
    assert paths.models_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.config_dir.is_dir()


def test_ensure_dirs_is_idempotent(tmp_path: Path) -> None:
    paths = AppPaths(root=tmp_path / "TT")
    paths.ensure_dirs()
    paths.ensure_dirs()  # second call must not raise


def test_first_run_marker_path_lives_under_config(tmp_path: Path) -> None:
    paths = AppPaths(root=tmp_path / "TT")
    assert paths.first_run_marker_path == tmp_path / "TT" / "config" / ".first-run-complete"


def test_runtime_dir_under_root() -> None:
    """runtime_dir is under root and ensure_dirs creates it."""
    import tempfile
    from pathlib import Path
    from teams_transcriber.paths import AppPaths

    with tempfile.TemporaryDirectory() as tmp:
        p = AppPaths(root=Path(tmp))
        assert p.runtime_dir == p.root / "runtime"
        p.ensure_dirs()
        assert p.runtime_dir.is_dir()
