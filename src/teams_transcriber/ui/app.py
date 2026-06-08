"""Qt application entry: wires Pipeline + tray + main window + hotkeys."""

from __future__ import annotations

import logging
import sys
import threading
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
    SummaryFailed,
    SummaryReady,
    TranscriptionComplete,
)
from teams_transcriber.meeting_watcher import MeetingWatcher, enumerate_windows
from teams_transcriber.paths import AppPaths
from teams_transcriber.pipeline import Pipeline
from teams_transcriber.storage import RecordingRepo, RecordingSource, RecordingStatus, SummaryRepo, TodoStateRepo, build_database
from teams_transcriber.storage.models import Recording
from teams_transcriber.summarizer import Summarizer
from teams_transcriber.transcriber import Transcriber
from teams_transcriber.ui.active_recording_banner import ActiveRecordingBanner
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
from teams_transcriber.ui.tray import AppTray

logger = logging.getLogger(__name__)


class _WorkspaceTracker:
    """Thread-safe set of recording ids that currently have an open notes window.

    The predicate is read from the recorder/watcher thread (via the pipeline
    gate); the set is mutated on the Qt main thread.
    """

    def __init__(self) -> None:
        self._ids: set[int] = set()
        self._lock = threading.Lock()

    def mark_open(self, recording_id: int) -> None:
        with self._lock:
            self._ids.add(recording_id)

    def mark_closed(self, recording_id: int) -> None:
        with self._lock:
            self._ids.discard(recording_id)

    def is_open(self, recording_id: int) -> bool:
        with self._lock:
            return recording_id in self._ids


def _default_export_name(title: str, started_at: str) -> str:
    import re
    from datetime import datetime
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "meeting").lower()).strip("-") or "meeting"
    try:
        day = datetime.fromisoformat(started_at).astimezone().strftime("%Y-%m-%d")
    except ValueError:
        day = "export"
    return f"{slug}-{day}.pdf"


def _wrike_should_offer_sync(
    *, enabled: bool, has_token: bool, already_synced: bool,
) -> bool:
    return enabled and has_token and not already_synced


def _wrike_lru_push(items: list[str], value: str, *, cap: int) -> list[str]:
    rest = [i for i in items if i != value]
    return ([value] + rest)[:cap]


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
        self._workspace_tracker = _WorkspaceTracker()
        self.pipeline = Pipeline(
            bus=self.bus, db=self.db, paths=self.paths, settings=self.settings,
            audio_source_factory=audio_factory,
            meeting_watcher=watcher,
            transcriber=Transcriber(bus=self.bus, db=self.db, settings=self.settings),
            summarizer=Summarizer(bus=self.bus, db=self.db, settings=self.settings),
            processing_gate=self._workspace_tracker.is_open,
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
        self.window.title_bar.settings_requested.connect(self._open_settings)

        # Tracks the currently-recording recording id so the tray notes action
        # and the toast "Add notes" button can find it.
        self._active_recording_id: int | None = None

        self.bridge.meeting_detected.connect(self._on_meeting_detected)
        self.bridge.recording_started.connect(self._on_recording_started)
        self.bridge.recording_finalized.connect(self._on_recording_finalized)
        self.bridge.recording_failed.connect(self._on_recording_failed)
        self.bridge.recording_device_fallback.connect(self._on_recording_device_fallback)
        self.bridge.transcription_complete.connect(self._on_transcription_complete)
        self.bridge.transcription_failed.connect(self._on_transcription_failed)
        self.bridge.summary_ready.connect(self._on_summary_ready)
        self.bridge.summary_ready.connect(self._on_summary_ready_wrike)
        self.bridge.summary_failed.connect(self._on_summary_failed)
        self.bridge.update_available.connect(self._on_update_available)
        self.bridge.update_check_completed.connect(self._on_update_check_completed)

        self.hotkeys = HotkeyManager()
        self._apply_hotkeys(self.settings.hotkeys)

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

        # Background update check on startup.
        if self.settings.auto_check_updates:
            threading.Thread(target=self._background_update_check, daemon=True).start()

    def _build_main_content(self) -> None:
        from PySide6.QtWidgets import QPushButton

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Top row: Record button + search
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.record_btn = QPushButton("Record")
        self.record_btn.setProperty("role", "primary")
        self.record_btn.setFixedHeight(36)
        self.record_btn.clicked.connect(self._toggle_manual)
        top_row.addWidget(self.record_btn)

        self.import_btn = QPushButton("Import audio…")
        self.import_btn.setProperty("role", "secondary")
        self.import_btn.setFixedHeight(36)
        self.import_btn.setToolTip(
            "Transcribe an audio file recorded outside Teams (phone, other "
            "device, or an orphaned recording in the audio folder)."
        )
        self.import_btn.clicked.connect(self._import_audio_file)
        top_row.addWidget(self.import_btn)

        self.search = SearchBar()
        self.search.query_changed.connect(self._on_search)
        top_row.addWidget(self.search, 1)
        layout.addLayout(top_row)

        self.active_banner = ActiveRecordingBanner()
        self.active_banner.clicked.connect(self._open_workspace)
        layout.addWidget(self.active_banner)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(16)
        self.history = HistoryList()
        self.history.recording_selected.connect(self._show_summary)
        self.summary = SummaryPane(self.db)
        self.summary.export_requested.connect(self._export_summary)
        self.summary.delete_requested.connect(self._delete_recording)
        self.summary.notes_requested.connect(self._open_workspace)
        self.summary.retry_requested.connect(self._retry_recording)
        self.summary.transcript_requested.connect(self._show_transcript)
        self.summary.todo_state_changed.connect(self._on_todo_state_changed)
        body_layout.addWidget(self.history, 1)
        body_layout.addWidget(self.summary, 1)

        from PySide6.QtWidgets import QStackedWidget
        from teams_transcriber.ui.master_todo_view import MasterTodoView

        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(body)                  # index 0
        self.master_todos = MasterTodoView(self.db)
        self._content_stack.addWidget(self.master_todos)     # index 1
        self.master_todos.go_to_summary.connect(self._go_to_summary_from_todos)
        self.master_todos.todo_toggled.connect(
            lambda _rid: self._refresh_history(query=self.search.input.text() or None)
        )
        layout.addWidget(self._content_stack, 1)

        self.window.set_content(content)
        self.window.sidebar.bucket_selected.connect(self._on_bucket)
        self.window.sidebar.todos_selected.connect(self._show_master_todos)

    def _refresh_history(self, query: str | None = None) -> None:
        rec_repo = RecordingRepo(self.db)
        sum_repo = SummaryRepo(self.db)
        todo_repo = TodoStateRepo(self.db)
        rows: list[tuple[Recording, str | None, int, int]] = []
        for rec in rec_repo.list_recent(limit=200):
            if rec.id is None:
                continue
            s = sum_repo.get(rec.id)
            one_line = s.one_line if s else None
            todos = len(s.my_todos) if s else 0
            done = sum(1 for st in todo_repo.list_for_recording(rec.id) if st.done) if s else 0
            rows.append((rec, one_line, todos, done))
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
        self._content_stack.setCurrentIndex(0)
        self._refresh_history(query=self.search.input.text() or None)

    def _on_todo_state_changed(self, _rid: int) -> None:
        self._refresh_history(query=self.search.input.text() or None)
        self.master_todos.reload()

    def _show_master_todos(self) -> None:
        self.master_todos.reload()
        self._content_stack.setCurrentIndex(1)

    def _go_to_summary_from_todos(self, recording_id: int) -> None:
        # Return to History (ALL so the card exists), select + show the meeting.
        self.window.sidebar.select_bucket(SidebarBucket.ALL)
        self._content_stack.setCurrentIndex(0)
        self._show_window()
        self.history.select(recording_id)

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

    def _apply_hotkeys(self, hotkey_map: dict[str, str]) -> None:
        self.hotkeys.reload([
            (hotkey_map.get("toggle_manual_recording", "ctrl+alt+r"),
             self._toggle_manual),
            (hotkey_map.get("open_workspace", "ctrl+alt+n"),
             self._open_workspace_for_active),
            (hotkey_map.get("toggle_pause_detection", "ctrl+alt+p"),
             self._toggle_pause_detection),
        ])

    def _toggle_pause_detection(self) -> None:
        watcher = self.pipeline._meeting_watcher  # noqa: SLF001
        if watcher is None:
            return
        new_paused = not getattr(watcher, "_paused", False)
        watcher.set_paused(new_paused)
        show_in_app_toast(
            "Detection paused" if new_paused else "Detection resumed",
            ("Teams meeting auto-recording is " +
             ("disabled until you resume." if new_paused else "active again.")),
        )

    def _on_pause_toggled(self, paused: bool) -> None:
        watcher = self.pipeline._meeting_watcher
        if watcher is not None:
            watcher.set_paused(paused)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(
            self.settings, self.paths,
            hotkey_reload_callback=self._on_hotkey_reload,
            parent=self.window,
        )
        dlg.saved.connect(self._refresh_history)
        dlg.exec()

    def _on_hotkey_reload(self, new_hotkeys: dict[str, str]) -> None:
        # Reload settings from disk (the dialog already persisted) and re-register.
        self.settings = load_settings(self.paths)
        self._apply_hotkeys(new_hotkeys)

    def _show_summary(self, recording_id: int) -> None:
        self._show_window()
        self.summary.show_recording(recording_id)

    def _import_audio_file(self) -> None:
        """Pick an external audio file and run it through the pipeline.

        Useful for recordings made outside Teams (phone, other device), or for
        recovering orphaned .opus files that exist in the audio dir but have
        no recording row (the file gets copied and a fresh row is created).
        """
        from pathlib import Path
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Import audio file",
            str(self.paths.audio_dir),
            "Audio (*.opus *.wav *.mp3 *.m4a *.flac *.ogg *.mp4);;All files (*.*)",
        )
        if not path:
            return
        try:
            rid = self.pipeline.import_audio_file(path)
        except FileNotFoundError:
            show_in_app_toast("Import failed", "That file no longer exists.")
            return
        except Exception as exc:
            logger.exception("import_audio_file failed for %r", path)
            show_in_app_toast(
                "Import failed",
                f"Couldn't read that file as audio: {exc}",
            )
            return
        show_in_app_toast(
            "Importing audio",
            f"Transcribing {Path(path).name} — you'll get a notification when it's ready.",
        )
        self._refresh_history(query=self.search.input.text() or None)
        # Highlight the new card.
        self.history.select(rid)

    def _export_summary(self, recording_id: int) -> None:
        rec = RecordingRepo(self.db).get(recording_id)
        s = SummaryRepo(self.db).get(recording_id)
        if rec is None or s is None:
            return
        default_name = _default_export_name(rec.display_title or s.title or "meeting", rec.started_at)
        path, _ = QFileDialog.getSaveFileName(
            self.window, "Export summary", default_name,
            "PDF (*.pdf);;Markdown (*.md);;Plain text (*.txt)",
        )
        if not path:
            return
        from teams_transcriber.storage import TodoStateRepo
        from teams_transcriber.ui.pdf_export import write_summary_export
        states = {
            st.todo_index: st.done
            for st in TodoStateRepo(self.db).list_for_recording(recording_id)
        }
        write_summary_export(path, s, rec, states)

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

    def _update_record_button(self) -> None:
        """Sync the Record/Stop button label to current recording state."""
        if self._active_recording_id is not None:
            self.record_btn.setText("Stop")
        else:
            self.record_btn.setText("Record")

    def _on_recording_started(self, evt: RecordingStarted) -> None:
        self.tray.set_state(TrayState.RECORDING, label=Path(evt.audio_path).stem)
        recording_id = evt.recording_id
        self._active_recording_id = recording_id
        rec = RecordingRepo(self.db).get(recording_id)
        is_manual = rec is not None and rec.source == RecordingSource.MANUAL
        title = (rec.display_title if rec else None) or (rec.detected_title if rec else None) or "Manual recording"
        self.active_banner.show_recording(recording_id, title, status_label="Recording")
        if is_manual:
            self._open_workspace(recording_id)
        show_in_app_toast(
            "Recording started",
            "Open workspace to take notes and watch live transcription.",
            action_label="Open workspace",
            action_callback=lambda: self._open_workspace(recording_id),
        )
        self._update_record_button()
        self._refresh_history()

    def _should_defer_processing(self, recording_id: int) -> bool:
        return self._workspace_tracker.is_open(recording_id)

    def _on_recording_finalized(self, _evt: RecordingFinalized) -> None:
        rid = self._active_recording_id
        self._active_recording_id = None
        deferred = rid is not None and self._should_defer_processing(rid)
        workspaces = getattr(self, "_workspace_windows", {})
        ws = workspaces.get(rid) if rid is not None else None
        if ws is not None:
            ws.set_recording_finished()
        if deferred:
            self.tray.set_state(TrayState.IDLE)
            self.active_banner.hide_banner()
            if ws is not None:
                ws.show_waiting_for_processing()
            show_in_app_toast(
                "Waiting for notes",
                "Transcription will start when you close the notes window.",
            )
        else:
            self.tray.set_state(TrayState.PROCESSING)
            self.active_banner.set_processing()
            show_in_app_toast(
                "Recording stopped",
                "Transcribing and summarizing — you'll get a notification when it's ready.",
            )
        self._update_record_button()
        self._refresh_history()

    def _on_recording_failed(self, evt: RecordingFailed) -> None:
        self.tray.set_state(TrayState.ERROR)
        self._active_recording_id = None
        msg = evt.error_message
        if "audio devices" in msg.lower():
            show_in_app_toast(
                "Recording failed", msg,
                action_label="Open Settings",
                action_callback=self._open_settings_audio_tab,
            )
        else:
            show_in_app_toast("Recording failed", msg)
        self.active_banner.hide_banner()
        self._update_record_button()
        self._refresh_history()

    def _on_recording_device_fallback(self, evt) -> None:
        channel_label = "microphone" if evt.channel == "microphone" else "system audio source"
        show_in_app_toast(
            f"Saved {channel_label} not connected",
            f"'{evt.requested_name}' is not available — using Windows default. "
            "Choose a different device in Settings → Audio.",
            action_label="Open Settings",
            action_callback=self._open_settings_audio_tab,
        )

    def _open_settings_audio_tab(self) -> None:
        """Open Settings and jump to the Audio tab."""
        from PySide6.QtWidgets import QTabWidget
        dlg = SettingsDialog(
            self.settings, self.paths,
            hotkey_reload_callback=self._on_hotkey_reload,
            parent=self.window,
        )
        for child in dlg.findChildren(QTabWidget):
            for i in range(child.count()):
                if child.tabText(i) == "Audio":
                    child.setCurrentIndex(i)
                    break
        dlg.saved.connect(self._refresh_history)
        dlg.exec()

    def _open_settings_transcription_tab(self) -> None:
        """Open Settings and jump to the Transcription tab.

        Used as the action button on the 'Whisper model couldn't load' toast
        so users land directly on the Re-download / model picker controls.
        """
        from PySide6.QtWidgets import QTabWidget
        dlg = SettingsDialog(
            self.settings, self.paths,
            hotkey_reload_callback=self._on_hotkey_reload,
            parent=self.window,
        )
        for child in dlg.findChildren(QTabWidget):
            for i in range(child.count()):
                if child.tabText(i) == "Transcription":
                    child.setCurrentIndex(i)
                    break
        dlg.saved.connect(self._refresh_history)
        dlg.exec()

    def _open_settings_ai_tab(self) -> None:
        """Open Settings and jump to the AI tab."""
        from PySide6.QtWidgets import QTabWidget
        dlg = SettingsDialog(
            self.settings, self.paths,
            hotkey_reload_callback=self._on_hotkey_reload,
            parent=self.window,
        )
        for child in dlg.findChildren(QTabWidget):
            for i in range(child.count()):
                if child.tabText(i) == "AI":
                    child.setCurrentIndex(i)
                    break
        dlg.saved.connect(self._refresh_history)
        dlg.exec()

    def _retry_recording(self, recording_id: int) -> None:
        """Re-run the failed step (transcription or summary) for a recording."""
        from teams_transcriber.storage import RecordingStatus
        rec = RecordingRepo(self.db).get(recording_id)
        if rec is None:
            return
        if rec.status == RecordingStatus.SUMMARY_FAILED:
            api_key = self.settings.anthropic_api_key()
            if not api_key:
                show_in_app_toast(
                    "Anthropic API key not configured",
                    "Open Settings → AI to add your key, then retry.",
                    action_label="Open Settings",
                    action_callback=self._open_settings_ai_tab,
                )
                return
            self.pipeline.retry_summary(recording_id, api_key=api_key)
            show_in_app_toast(
                "Retrying summary",
                "Re-running summarization — you'll get a notification when it's ready.",
            )
            title = (rec.display_title if rec else None) or "Meeting"
            self.active_banner.show_recording(recording_id, title, status_label="Recording")
            self.active_banner.set_processing()
        elif rec.status == RecordingStatus.TRANSCRIPTION_FAILED:
            self.pipeline.retry_transcription(recording_id)
            show_in_app_toast(
                "Retrying transcription",
                "Re-running transcription — you'll get a notification when it's ready.",
            )
            title = (rec.display_title if rec else None) or "Meeting"
            self.active_banner.show_recording(recording_id, title, status_label="Recording")
            self.active_banner.set_processing()
        self._refresh_history()

    def _on_transcription_complete(self, _evt: TranscriptionComplete) -> None:
        self.tray.set_state(TrayState.PROCESSING)
        self._refresh_history()

    def _on_transcription_failed(self, evt) -> None:
        self.tray.set_state(TrayState.ERROR)
        if self.active_banner.current_recording_id() == evt.recording_id:
            self.active_banner.hide_banner()
        msg = evt.error_message or ""
        if "model.bin" in msg.lower():
            # Specific, actionable UX for the Whisper-model-file failure
            # (model never finished downloading, antivirus quarantined it,
            # dangling cache symlink, etc.).
            show_in_app_toast(
                "Whisper model couldn't load",
                "The Whisper model file couldn't be opened. Open Settings → "
                "Transcription to re-download it (or pick a smaller model). "
                "If your antivirus may be quarantining model.bin, add the "
                ".cache\\huggingface folder to its exclusions first.",
                action_label="Open Settings",
                action_callback=self._open_settings_transcription_tab,
            )
        else:
            show_in_app_toast("Transcription failed", msg)
        self._refresh_history()

    def _on_summary_failed(self, evt: SummaryFailed) -> None:
        self.tray.set_state(TrayState.ERROR)
        if self.active_banner.current_recording_id() == evt.recording_id:
            self.active_banner.hide_banner()
        if "api key" in evt.error_message.lower():
            show_in_app_toast(
                "Summary failed", evt.error_message,
                action_label="Open Settings",
                action_callback=self._open_settings_ai_tab,
            )
        else:
            show_in_app_toast("Summary failed", evt.error_message)
        self._refresh_history()

    def _on_summary_ready(self, evt: SummaryReady) -> None:
        self.tray.set_state(TrayState.IDLE)
        if (
            self.active_banner.current_recording_id() == evt.recording_id
        ):
            self.active_banner.hide_banner()
        rec = RecordingRepo(self.db).get(evt.recording_id)
        title = (rec.display_title if rec else None) or "Meeting"
        recording_id = evt.recording_id
        show_in_app_toast(
            "Summary ready", title,
            action_label="Open",
            action_callback=lambda: self._show_summary(recording_id),
        )
        self._refresh_history()

    def _on_summary_ready_wrike(self, evt) -> None:
        """Offer to sync this summary's todos to Wrike via a toast + picker."""
        import keyring
        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE
        from teams_transcriber.storage import SummaryRepo
        from teams_transcriber.storage.wrike import WrikeSyncRepo

        token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
        enabled = bool(
            self.settings._raw.get("integrations", {}).get("wrike_enabled", False)
        )
        existing = WrikeSyncRepo(self.db).get(evt.recording_id)
        already_synced = bool(existing and existing.status == "synced")
        if not _wrike_should_offer_sync(
            enabled=enabled, has_token=bool(token), already_synced=already_synced,
        ):
            return
        s = SummaryRepo(self.db).get(evt.recording_id)
        if s is None:
            return
        n = len(s.my_todos) + len(s.action_items_others)
        if n == 0:
            return
        WrikeSyncRepo(self.db).upsert(evt.recording_id, status="pending")
        rid = evt.recording_id
        show_in_app_toast(
            "Send todos to Wrike",
            f"{n} task{'s' if n != 1 else ''} ready — pick a folder.",
            action_label="Pick folder",
            action_callback=lambda: self._wrike_open_picker(rid),
        )

    def _wrike_open_picker(self, recording_id: int) -> None:
        """Open the folder picker; on accept, run the sync in a background thread."""
        import keyring
        from teams_transcriber.config import (
            KEYRING_SERVICE, KEYRING_USER_WRIKE, save_settings,
        )
        from teams_transcriber.integrations.wrike_client import (
            WrikeApiError,
            WrikeClient,
        )
        from teams_transcriber.storage.wrike import WrikeSyncRepo
        from teams_transcriber.ui.wrike_folder_picker import WrikeFolderPicker

        token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
        if not token:
            show_in_app_toast(
                "Wrike not configured",
                "Add a token in Settings → Integrations.",
            )
            return

        client = WrikeClient(token=token)
        try:
            folders = client.list_folders()
        except WrikeApiError as exc:
            client.close()
            show_in_app_toast("Wrike error", str(exc))
            WrikeSyncRepo(self.db).update(
                recording_id, status="failed", error_message=str(exc),
            )
            return
        client.close()

        recent_ids = list(
            self.settings._raw.get("integrations", {})
            .get("wrike_recent_folder_ids", []) or []
        )
        dlg = WrikeFolderPicker(
            folders=folders, recent_folder_ids=recent_ids, parent=self.window,
        )
        if dlg.exec() != dlg.DialogCode.Accepted or not dlg.selected_folder_id:
            return
        folder_id = dlg.selected_folder_id
        new_recent = _wrike_lru_push(recent_ids, folder_id, cap=5)
        self.settings._raw.setdefault("integrations", {})[
            "wrike_recent_folder_ids"
        ] = new_recent
        save_settings(self.paths, self.settings)

        threading.Thread(
            target=self._wrike_run_sync,
            args=(recording_id, folder_id, token),
            daemon=True,
        ).start()

    def _wrike_run_sync(
        self, recording_id: int, folder_id: str, token: str,
    ) -> None:
        """Background-thread sync. Updates wrike_sync status + toasts the result."""
        from teams_transcriber.integrations.wrike_client import (
            WrikeApiError,
            WrikeClient,
        )
        from teams_transcriber.integrations.wrike_sync import sync_recording
        from teams_transcriber.storage.wrike import WrikeSyncRepo

        client = WrikeClient(token=token)
        try:
            result = sync_recording(
                self.db, client, recording_id, folder_id=folder_id,
            )
            WrikeSyncRepo(self.db).update(
                recording_id, status="synced", folder_id=folder_id,
            )
            n = result.created_my + result.created_other
            extra = (
                f" — {result.assigned_other} assigned"
                if result.assigned_other else ""
            )
            show_in_app_toast(
                "Synced to Wrike",
                f"Created {n} task{'s' if n != 1 else ''}{extra}",
            )
        except WrikeApiError as exc:
            WrikeSyncRepo(self.db).update(
                recording_id, status="failed", error_message=str(exc),
            )
            show_in_app_toast("Wrike sync failed", str(exc))
        finally:
            client.close()

    def _background_update_check(self) -> None:
        from datetime import UTC, datetime

        from teams_transcriber import __version__
        from teams_transcriber.events import UpdateAvailable, UpdateCheckCompleted
        from teams_transcriber.update_checker import (
            UpdateCheckError,
            fetch_latest_release,
            is_update_available,
        )

        try:
            latest = fetch_latest_release()
        except UpdateCheckError as exc:
            logger.warning("update check failed: %s", exc)
            return

        now_iso = datetime.now(UTC).isoformat()
        if is_update_available(__version__, latest):
            self.bus.publish(UpdateAvailable(
                version=latest.tag,
                download_url=latest.installer_url,
                release_url=latest.html_url,
            ))
        self.bus.publish(UpdateCheckCompleted(
            latest_version=(latest.tag if is_update_available(__version__, latest) else None),
            checked_at=now_iso,
        ))

    def _on_update_available(self, evt) -> None:
        show_in_app_toast(
            f"Update available: {evt.version}",
            "Click Install to download the latest installer.",
            action_label="Install",
            action_callback=lambda: self._start_update_download(evt),
        )

    def _on_update_check_completed(self, evt) -> None:
        # Persist last_update_check.
        self.settings._raw["general"]["last_update_check"] = evt.checked_at
        from teams_transcriber.config import save_settings
        save_settings(self.paths, self.settings)

    def _start_update_download(self, evt) -> None:
        from teams_transcriber.ui.update_dialog import UpdateDialog
        dlg = UpdateDialog(
            version=evt.version,
            download_url=evt.download_url,
            paths=self.paths,
            parent=self.window,
        )
        dlg.exec()

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
            settings=self.settings,
        )
        win.stop_recording_requested.connect(lambda _rid: self._stop_manual())
        win.closed.connect(self._on_workspace_closed)
        self._workspace_windows = getattr(self, "_workspace_windows", {})
        self._workspace_windows[recording_id] = win
        self._workspace_tracker.mark_open(recording_id)
        win.show()

    def _on_workspace_closed(self, recording_id: int) -> None:
        windows = getattr(self, "_workspace_windows", {})
        windows.pop(recording_id, None)
        self._workspace_tracker.mark_closed(recording_id)
        rec = RecordingRepo(self.db).get(recording_id)
        was_waiting = rec is not None and rec.status == RecordingStatus.WAITING_FOR_NOTES
        self.pipeline.release_processing(recording_id)
        if was_waiting:
            self.tray.set_state(TrayState.PROCESSING)
            self.active_banner.set_processing()
            show_in_app_toast(
                "Processing started",
                "Transcribing and summarizing your meeting now.",
            )
        self._refresh_history()

    def _show_transcript(self, recording_id: int) -> None:
        from teams_transcriber.ui.transcript_window import TranscriptWindow
        win = TranscriptWindow(db=self.db, recording_id=recording_id)
        win.show()
        # Keep a reference so it doesn't get garbage-collected.
        self._transcript_windows = getattr(self, "_transcript_windows", {})
        self._transcript_windows[recording_id] = win

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
