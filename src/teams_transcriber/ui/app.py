"""Qt application entry: wires Pipeline + tray + main window + hotkeys."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from teams_transcriber.audio.source import RealAudioSource
from teams_transcriber.config import load_settings
from teams_transcriber.events import (
    EventBus,
    MeetingDetected,
    RecordingFailed,
    RecordingFinalized,
    RecordingStarted,
    SummaryReady,
    TranscriptionComplete,
)
from teams_transcriber.meeting_watcher import MeetingWatcher, enumerate_windows
from teams_transcriber.paths import AppPaths
from teams_transcriber.pipeline import Pipeline
from teams_transcriber.storage import RecordingRepo, RecordingSource, RecordingStatus, SummaryRepo, build_database
from teams_transcriber.storage.models import Recording
from teams_transcriber.summarizer import Summarizer
from teams_transcriber.transcriber import Transcriber
from teams_transcriber.ui.confirm_dialog import ConfirmDialog
from teams_transcriber.ui.history_list import HistoryList, filter_for_bucket
from teams_transcriber.ui.hotkeys import HotkeyManager
from teams_transcriber.ui.icons import TrayState
from teams_transcriber.ui.main_window import MainWindow
from teams_transcriber.ui.qt_bridge import QtEventBridge
from teams_transcriber.ui.workspace_window import WorkspaceWindow
from teams_transcriber.ui.search_bar import SearchBar
from teams_transcriber.ui.settings_dialog import SettingsDialog
from teams_transcriber.ui.sidebar import SidebarBucket
from teams_transcriber.ui.summary_pane import SummaryPane
from teams_transcriber.ui.theme import app_stylesheet
from teams_transcriber.ui.toast_banner import show_in_app_toast
from teams_transcriber.ui.transcript_view import TranscriptView
from teams_transcriber.ui.tray import AppTray

logger = logging.getLogger(__name__)


def _make_app() -> QApplication:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([])
    app.setApplicationName("Teams Transcriber")
    app.setOrganizationName("Teams Transcriber")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(app_stylesheet())
    return app


class App:
    """Top-level wiring: owns QApplication, Pipeline, tray, main window."""

    def __init__(self) -> None:
        self.paths = AppPaths()
        self.paths.ensure_dirs()
        self.settings = load_settings(self.paths)
        self.db = build_database(self.paths.db_path)
        self.db.initialize()
        self.bus = EventBus()

        self.qapp = _make_app()
        self.bridge = QtEventBridge(self.bus)
        self.tray = AppTray()
        self.tray.show()

        def audio_factory() -> Any:
            return RealAudioSource.from_default_devices()

        watcher = MeetingWatcher(
            bus=self.bus,
            current_windows=enumerate_windows,
            title_patterns=self.settings.detection_title_patterns,
            debounce_polls=self.settings.detection_debounce_polls,
            poll_interval_ms=self.settings.detection_poll_interval_ms,
        )
        self.pipeline = Pipeline(
            bus=self.bus, db=self.db, paths=self.paths, settings=self.settings,
            audio_source_factory=audio_factory,
            meeting_watcher=watcher,
            transcriber=Transcriber(bus=self.bus, db=self.db, settings=self.settings),
            summarizer=Summarizer(bus=self.bus, db=self.db, settings=self.settings),
        )

        self.window = MainWindow()
        self._build_main_content()

        self.tray.open_window_requested.connect(self._show_window)
        self.tray.start_manual_requested.connect(self._start_manual)
        self.tray.stop_manual_requested.connect(self._stop_manual)
        self.tray.pause_detection_toggled.connect(self._on_pause_toggled)
        self.tray.open_workspace_requested.connect(self._open_workspace_for_active)
        self.tray.quit_requested.connect(self._quit)
        self.tray.settings_action.triggered.connect(self._open_settings)

        # Tracks the currently-recording recording id so the tray notes action
        # and the toast "Add notes" button can find it.
        self._active_recording_id: int | None = None

        self.bridge.meeting_detected.connect(self._on_meeting_detected)
        self.bridge.recording_started.connect(self._on_recording_started)
        self.bridge.recording_finalized.connect(self._on_recording_finalized)
        self.bridge.recording_failed.connect(self._on_recording_failed)
        self.bridge.transcription_complete.connect(self._on_transcription_complete)
        self.bridge.summary_ready.connect(self._on_summary_ready)

        self.hotkeys = HotkeyManager()
        self.hotkeys.register(
            self.settings._raw["hotkeys"]["toggle_manual_recording"],
            self._toggle_manual,
        )

        if not self.paths.first_run_marker_path.exists():
            from teams_transcriber.ui.first_run_wizard import FirstRunWizard
            wizard = FirstRunWizard(
                settings=self.settings, paths=self.paths, parent=self.window,
            )
            wizard.exec()
            # Wizard wrote to disk and synced the registry; reload settings.
            self.settings = load_settings(self.paths)

        if self.settings.auto_launch:
            from teams_transcriber import autolaunch
            autolaunch.enable()

        self.pipeline.serve()
        self._refresh_history()

    def _build_main_content(self) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.search = SearchBar()
        self.search.query_changed.connect(self._on_search)
        layout.addWidget(self.search)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(16)
        self.history = HistoryList()
        self.history.recording_selected.connect(self._show_summary)
        self.summary = SummaryPane(self.db)
        self.summary.transcript_requested.connect(self._show_transcript)
        self.summary.export_requested.connect(self._export_summary)
        self.summary.delete_requested.connect(self._delete_recording)
        self.summary.notes_requested.connect(self._open_workspace)
        body_layout.addWidget(self.history, 1)
        body_layout.addWidget(self.summary, 1)
        layout.addWidget(body, 1)

        self.window.set_content(content)
        self.window.sidebar.bucket_selected.connect(self._on_bucket)

    def _refresh_history(self, query: str | None = None) -> None:
        rec_repo = RecordingRepo(self.db)
        sum_repo = SummaryRepo(self.db)
        rows: list[tuple[Recording, str | None, int]] = []
        for rec in rec_repo.list_recent(limit=200):
            if rec.id is None:
                continue
            s = sum_repo.get(rec.id)
            one_line = s.one_line if s else None
            todos = len(s.my_todos) if s else 0
            rows.append((rec, one_line, todos))
        if query:
            ql = query.lower()
            rows = [
                r for r in rows
                if (r[0].display_title and ql in r[0].display_title.lower())
                or (r[1] and ql in r[1].lower())
            ]
        bucket = self.window.sidebar.active_bucket
        rows = filter_for_bucket(rows, bucket)
        self.history.set_recordings(rows)

    def _on_search(self, text: str) -> None:
        self._refresh_history(query=text or None)

    def _on_bucket(self, _bucket: SidebarBucket) -> None:
        self._refresh_history()

    def _show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _start_manual(self) -> None:
        self.pipeline.start_manual()

    def _stop_manual(self) -> None:
        self.pipeline.stop_manual()

    def _toggle_manual(self) -> None:
        if self.tray.state == TrayState.RECORDING:
            self._stop_manual()
        else:
            self._start_manual()

    def _on_pause_toggled(self, paused: bool) -> None:
        watcher = self.pipeline._meeting_watcher
        if watcher is not None:
            watcher.set_paused(paused)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self.paths, parent=self.window)
        dlg.saved.connect(self._refresh_history)
        dlg.exec()

    def _show_summary(self, recording_id: int) -> None:
        self._show_window()
        self.summary.show_recording(recording_id)

    def _show_transcript(self, recording_id: int) -> None:
        view = TranscriptView(self.db)
        view.setWindowFlags(Qt.WindowType.Dialog)
        view.setWindowTitle("Transcript")
        view.resize(700, 600)
        view.show_recording(recording_id)
        view.show()
        # Keep a reference so it isn't garbage-collected immediately.
        self._transcript_window = view

    def _export_summary(self, recording_id: int) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self.window, "Export summary",
            f"meeting-{recording_id}.md",
            "Markdown (*.md);;Plain text (*.txt)",
        )
        if not path:
            return
        rec = RecordingRepo(self.db).get(recording_id)
        s = SummaryRepo(self.db).get(recording_id)
        if rec is None or s is None:
            return
        lines = [
            f"# {s.title or rec.display_title or 'Meeting'}", "",
            f"_{rec.started_at} · {(rec.duration_ms or 0)/60000:.0f} min_", "",
            s.summary or "", "", "## My todos",
        ]
        for t in s.my_todos:
            lines.append(f"- [ ] {t.task}" + (f" (due {t.due})" if t.due else ""))
        lines += ["", "## Action items for others"]
        for a in s.action_items_others:
            lines.append(f"- {a.who}: {a.task}" + (f" (due {a.due})" if a.due else ""))
        lines += ["", "## Key decisions"]
        lines += [f"- {d}" for d in s.key_decisions]
        lines += ["", "## Follow-ups"]
        lines += [f"- {f}" for f in s.follow_ups]
        Path(path).write_text("\n".join(lines), encoding="utf-8")

    def _delete_recording(self, recording_id: int) -> None:
        """Confirm and delete a recording (DB row + audio file). Cascading delete
        removes the summary, transcript segments, and todo states."""
        rec_repo = RecordingRepo(self.db)
        rec = rec_repo.get(recording_id)
        if rec is None:
            return
        title = rec.display_title or rec.detected_title or "this recording"
        confirmed = ConfirmDialog.ask(
            self.window,
            title="Delete recording?",
            body=(
                f"Permanently delete “{title}”, its transcript, summary, "
                "and notes? The audio file on disk will also be removed."
            ),
            confirm_label="Delete",
            cancel_label="Cancel",
            danger=True,
        )
        if not confirmed:
            return

        if rec.audio_path:
            audio = Path(rec.audio_path)
            if audio.exists():
                try:
                    audio.unlink()
                except OSError:
                    logger.exception("could not delete audio file %s", audio)
        rec_repo.delete(recording_id)
        self.summary.clear()
        self._refresh_history()

    def _on_meeting_detected(self, evt: MeetingDetected) -> None:
        # Toast appears when the recorder actually starts (we have the recording_id then).
        # No-op here — _on_recording_started handles the toast.
        del evt

    def _on_recording_started(self, evt: RecordingStarted) -> None:
        self.tray.set_state(TrayState.RECORDING, label=Path(evt.audio_path).stem)
        recording_id = evt.recording_id
        self._active_recording_id = recording_id
        rec = RecordingRepo(self.db).get(recording_id)
        is_manual = rec is not None and rec.source == RecordingSource.MANUAL
        if is_manual:
            self._open_workspace(recording_id)
        show_in_app_toast(
            "Recording started",
            "Open workspace to take notes and watch live transcription.",
            action_label="Open workspace",
            action_callback=lambda: self._open_workspace(recording_id),
        )
        self._refresh_history()

    def _on_recording_finalized(self, _evt: RecordingFinalized) -> None:
        self.tray.set_state(TrayState.PROCESSING)
        rid = self._active_recording_id
        self._active_recording_id = None
        show_in_app_toast(
            "Recording stopped",
            "Transcribing and summarizing — you'll get a notification when it's ready.",
        )
        if rid is not None:
            workspaces = getattr(self, "_workspace_windows", {})
            ws = workspaces.get(rid)
            if ws is not None:
                ws.set_recording_finished()
        self._refresh_history()

    def _on_recording_failed(self, evt: RecordingFailed) -> None:
        self.tray.set_state(TrayState.ERROR)
        show_in_app_toast("Recording failed", evt.error_message)
        self._refresh_history()

    def _on_transcription_complete(self, _evt: TranscriptionComplete) -> None:
        self.tray.set_state(TrayState.PROCESSING)
        self._refresh_history()

    def _on_summary_ready(self, evt: SummaryReady) -> None:
        self.tray.set_state(TrayState.IDLE)
        rec = RecordingRepo(self.db).get(evt.recording_id)
        title = (rec.display_title if rec else None) or "Meeting"
        recording_id = evt.recording_id
        show_in_app_toast(
            "Summary ready", title,
            action_label="Open",
            action_callback=lambda: self._show_summary(recording_id),
        )
        self._refresh_history()

    def _open_workspace_for_active(self) -> None:
        if self._active_recording_id is not None:
            self._open_workspace(self._active_recording_id)
            return
        recents = RecordingRepo(self.db).list_recent(limit=1)
        if recents and recents[0].id is not None:
            self._open_workspace(recents[0].id)
        else:
            show_in_app_toast(
                "Nothing to show yet",
                "Start a recording to open the workspace.",
            )

    def _open_workspace(self, recording_id: int) -> None:
        """Open (or raise) the workspace window for a recording.

        Live mode if the recording is still recording, past mode otherwise.
        """
        existing = getattr(self, "_workspace_windows", {}).get(recording_id)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return

        rec = RecordingRepo(self.db).get(recording_id)
        live = (rec is not None and rec.status == RecordingStatus.RECORDING)
        win = WorkspaceWindow(
            db=self.db,
            recording_id=recording_id,
            bridge=self.bridge,
            live=live,
        )
        win.stop_recording_requested.connect(lambda _rid: self._stop_manual())
        win.closed.connect(self._on_workspace_closed)
        self._workspace_windows = getattr(self, "_workspace_windows", {})
        self._workspace_windows[recording_id] = win
        win.show()

    def _on_workspace_closed(self, recording_id: int) -> None:
        windows = getattr(self, "_workspace_windows", {})
        windows.pop(recording_id, None)
        self._refresh_history()

    def _quit(self) -> None:
        self.hotkeys.stop()
        self.pipeline.shutdown()
        self.db.close()
        self.qapp.quit()

    def run(self) -> int:
        return int(self.qapp.exec())


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = App()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
