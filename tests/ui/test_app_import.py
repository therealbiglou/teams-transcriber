"""Capture entry points restored on the simplified main window: the
top-of-window Record and Import buttons. Mirrors the App.__new__ +
SimpleNamespace pattern used elsewhere in tests/ui/ -- no full App()
construction (tray/pipeline/first-run wizard).
"""

from __future__ import annotations

from types import SimpleNamespace

from teams_transcriber.ui.icons import TrayState
from teams_transcriber.ui.main_window import MainWindow


def _bare_app(tmp_path):
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.ui.app import App

    app = App.__new__(App)
    app.paths = AppPaths(root=tmp_path)
    app.paths.ensure_dirs()
    app.window = MainWindow()
    app._refresh_history = lambda: None  # no db wired up for these tests
    return app


def test_import_audio_file_calls_pipeline_import_audio(tmp_path, monkeypatch, qapp):
    app = _bare_app(tmp_path)
    audio_path = str(tmp_path / "call.wav")
    monkeypatch.setattr(
        "teams_transcriber.ui.app.QFileDialog.getOpenFileName",
        lambda *a, **kw: (audio_path, ""),
    )
    calls: list[str] = []
    app.pipeline = SimpleNamespace(
        import_audio_file=lambda p: calls.append(p),
        import_transcript_file=lambda p: (_ for _ in ()).throw(
            AssertionError("should not import as transcript")
        ),
    )

    app._import_audio_file()

    assert calls == [audio_path]


def test_import_vtt_file_routes_to_transcript(tmp_path, monkeypatch, qapp):
    app = _bare_app(tmp_path)
    vtt_path = str(tmp_path / "meeting.vtt")
    monkeypatch.setattr(
        "teams_transcriber.ui.app.QFileDialog.getOpenFileName",
        lambda *a, **kw: (vtt_path, ""),
    )
    calls: list[str] = []
    app.pipeline = SimpleNamespace(
        import_transcript_file=lambda p: calls.append(p),
        import_audio_file=lambda p: (_ for _ in ()).throw(
            AssertionError("should not import as audio")
        ),
    )

    app._import_audio_file()

    assert calls == [vtt_path]


def test_import_cancelled_calls_neither(tmp_path, monkeypatch, qapp):
    app = _bare_app(tmp_path)
    monkeypatch.setattr(
        "teams_transcriber.ui.app.QFileDialog.getOpenFileName",
        lambda *a, **kw: ("", ""),
    )
    app.pipeline = SimpleNamespace(
        import_audio_file=lambda p: (_ for _ in ()).throw(
            AssertionError("must not be called when the dialog is cancelled")
        ),
        import_transcript_file=lambda p: (_ for _ in ()).throw(
            AssertionError("must not be called when the dialog is cancelled")
        ),
    )

    app._import_audio_file()  # no-op, no exception


def test_record_button_triggers_same_handler_tray_uses(tmp_path, qapp):
    app = _bare_app(tmp_path)
    app.tray = SimpleNamespace(state=TrayState.IDLE)
    started: list[bool] = []
    app.pipeline = SimpleNamespace(
        start_manual=lambda: started.append(True),
        stop_manual=lambda: started.append(False),
    )
    app.window.record_requested.connect(app._toggle_manual)

    app.window.record_btn.click()

    assert started == [True]  # same _start_manual -> pipeline.start_manual the tray uses


def test_import_button_triggers_import_handler(tmp_path, monkeypatch, qapp):
    app = _bare_app(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(app, "_import_audio_file", lambda: calls.append("import"))
    app.window.import_requested.connect(app._import_audio_file)

    app.window.import_btn.click()

    assert calls == ["import"]
