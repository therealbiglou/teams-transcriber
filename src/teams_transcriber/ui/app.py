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
from teams_transcriber.storage import (
    RecordingRepo,
    RecordingSource,
    RecordingStatus,
    SummaryRepo,
    TodoStateRepo,
    build_database,
)
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
from teams_transcriber.ui.scrim import exec_modal
from teams_transcriber.ui.search_bar import SearchBar
from teams_transcriber.ui.settings_dialog import SettingsDialog
from teams_transcriber.ui.sidebar import SidebarBucket
from teams_transcriber.ui.summary_pane import SummaryPane
from teams_transcriber.ui.theme import app_stylesheet
from teams_transcriber.ui.toast_banner import show_in_app_toast
from teams_transcriber.ui.tray import AppTray
from teams_transcriber.ui.workspace_window import WorkspaceWindow

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


def _chat_should_send(*, api_key: str, text: str) -> bool:
    return bool(api_key) and bool(text.strip())


def _build_columns_splitter(history, summary):
    """History | Summary as a user-resizable splitter (was a fixed 50/50 box)."""
    from PySide6.QtWidgets import QSplitter
    sp = QSplitter(Qt.Orientation.Horizontal)
    sp.setHandleWidth(6)
    sp.setChildrenCollapsible(False)
    sp.addWidget(history)
    sp.addWidget(summary)
    sp.setStretchFactor(0, 1)
    sp.setStretchFactor(1, 1)
    return sp


def _wrike_pick_pending(rows: list) -> int | None:
    """Return the recording_id of the oldest pending/failed sync, or None."""
    pending = [r for r in rows if r.status in ("pending", "failed")]
    if not pending:
        return None
    pending.sort(key=lambda r: r.last_attempted_at or "")
    return pending[0].recording_id


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
            # from_settings resolves the saved mic/loopback (id → name →
            # Windows default) and records fallbacks for the warning toast.
            return RealAudioSource.from_settings(self.settings)

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

        # Guards _wrike_export_worker against two concurrent workers for the
        # same recording -- SummaryReady auto-push, the manual Send button,
        # the partial-failure toast's Retry, and the startup pending-sync
        # toast can all reach it back-to-back for the same recording_id, and
        # two workers both passing the "no project yet" check before either
        # persists would create a duplicate Wrike project.
        self._wrike_exports_in_flight: set[int] = set()

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
            exec_modal(wizard)
            # Wizard wrote to disk and synced the registry; reload settings.
            self.settings = load_settings(self.paths)

        if self.settings.auto_launch:
            from teams_transcriber import autolaunch
            autolaunch.enable()

        self.pipeline.serve()
        self._refresh_history()

        # Offer to retry any pending/failed Wrike syncs (consolidated toast).
        try:
            from teams_transcriber.storage.wrike import WrikeSyncRepo
            pending = WrikeSyncRepo(self.db).list_pending_or_failed()
            rid = _wrike_pick_pending(pending)
            if rid is not None:
                count = len(pending)
                show_in_app_toast(
                    "Pending Wrike syncs",
                    f"{count} meeting{'s' if count != 1 else ''} waiting.",
                    action_label="Retry",
                    action_callback=lambda r=rid: self._wrike_export_worker(r),
                )
        except Exception:
            logger.exception("pending-Wrike-syncs check failed")

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

        self.import_btn = QPushButton("Import…")
        self.import_btn.setProperty("role", "secondary")
        self.import_btn.setFixedHeight(36)
        self.import_btn.setToolTip(
            "Import an audio file (.opus/.wav/.mp3/.m4a/.flac/.ogg/.mp4) "
            "to transcribe + summarize, OR a transcript file "
            "(.txt/.md/.vtt/.srt) to summarize directly."
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

        self.history = HistoryList()
        self.history.recording_selected.connect(self._show_summary)
        self.summary = SummaryPane(
            self.db,
            wrike_available=self._wrike_project_enabled,
            anthropic_key_getter=self._anthropic_key,
        )
        self.summary.export_requested.connect(self._export_summary)
        self.summary.delete_requested.connect(self._delete_recording)
        self.summary.notes_requested.connect(self._open_workspace)
        self.summary.retry_requested.connect(self._retry_recording)
        self.summary.transcript_requested.connect(self._show_transcript)
        self.summary.todo_state_changed.connect(self._on_todo_state_changed)
        self.summary.wrike_sync_requested.connect(self._wrike_export_worker)
        self.summary.chat_send_requested.connect(self._on_chat_send)
        from teams_transcriber.ui.window_state import (
            restore_splitter_state,
            save_splitter_state,
        )
        body = _build_columns_splitter(self.history, self.summary)
        restore_splitter_state(body, "main_columns")
        body.splitterMoved.connect(
            lambda *_: save_splitter_state(body, "main_columns")
        )

        from PySide6.QtWidgets import QStackedWidget

        from teams_transcriber.ui.master_todo_view import MasterTodoView

        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(body)                  # index 0
        self.master_todos = MasterTodoView(self.db)
        self._content_stack.addWidget(self.master_todos)     # index 1
        self.master_todos.go_to_summary.connect(self._go_to_summary_from_todos)
        self.master_todos.todo_toggled.connect(self._on_master_todo_toggled)
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
            # Bound the done count to the CURRENT summary's todos: todo_state
            # keeps stale rows for indices beyond a shrunk my_todos (seed
            # never prunes on re-summarization), and those must not inflate
            # the history chip's done count.
            done = (
                sum(
                    1 for st in todo_repo.list_for_recording(rec.id)
                    if st.done and st.todo_index < todos
                )
                if s else 0
            )
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

    def _on_todo_state_changed(self, rid: int) -> None:
        self._refresh_history(query=self.search.input.text() or None)
        self.master_todos.reload()

    def _on_master_todo_toggled(self, recording_id: int) -> None:
        """Master-view toggle: refresh history's done-count chip.
        No master_todos.reload() here — the toggled checkbox is the sender and
        reload would delete it mid-signal; the view already shows the new state."""
        self._refresh_history(query=self.search.input.text() or None)

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

    def _marshal(self, fn):
        """Wrap a callback so it executes on the Qt main thread.

        Global hotkeys fire on the keyboard library's listener thread;
        creating QWidgets there is undefined behavior. The 3-arg singleShot
        binds the timer to self.window's (main) thread — same pattern as the
        worker-thread hops elsewhere in this file.
        """
        from PySide6.QtCore import QTimer
        return lambda: QTimer.singleShot(0, self.window, fn)

    def _apply_hotkeys(self, hotkey_map: dict[str, str]) -> None:
        self.hotkeys.reload([
            (hotkey_map.get("toggle_manual_recording", "ctrl+alt+r"),
             self._marshal(self._toggle_manual)),
            (hotkey_map.get("open_workspace", "ctrl+alt+n"),
             self._marshal(self._open_workspace_for_active)),
            (hotkey_map.get("toggle_pause_detection", "ctrl+alt+p"),
             self._marshal(self._toggle_pause_detection)),
        ])

    def _toggle_pause_detection(self) -> None:
        watcher = self.pipeline._meeting_watcher
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
        self._open_settings_tab(None)

    def _open_settings_tab(self, tab: str | None) -> None:
        """Open Settings, optionally jumping to a named tab."""
        from PySide6.QtWidgets import QTabWidget
        dlg = SettingsDialog(
            self.settings, self.paths,
            hotkey_reload_callback=self._on_hotkey_reload,
            update_quit_callback=self._quit_for_update,
            parent=self.window,
        )
        if tab is not None:
            for child in dlg.findChildren(QTabWidget):
                for i in range(child.count()):
                    if child.tabText(i) == tab:
                        child.setCurrentIndex(i)
                        break
        dlg.saved.connect(self._refresh_history)
        exec_modal(dlg)

    def _on_hotkey_reload(self, new_hotkeys: dict[str, str]) -> None:
        # Reload settings from disk (the dialog already persisted) and re-register.
        self.settings = load_settings(self.paths)
        self._apply_hotkeys(new_hotkeys)

    def _show_summary(self, recording_id: int) -> None:
        self._show_window()
        self.summary.show_recording(recording_id)

    def _import_audio_file(self) -> None:
        """Pick an external audio OR transcript file and run it through the pipeline.

        Audio files (.opus/.wav/.mp3/.m4a/.flac/.ogg/.mp4) are copied into the
        audio dir, transcribed, then summarized — useful for phone recordings,
        other devices, or recovering orphaned .opus files. Transcript files
        (.txt/.md/.vtt/.srt) skip transcription entirely and go straight to
        the summarizer — useful for transcripts exported from another tool.
        """
        from pathlib import Path

        from teams_transcriber.transcript_importer import is_transcript_file
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Import",
            str(self.paths.audio_dir),
            (
                "Audio or transcript "
                "(*.opus *.wav *.mp3 *.m4a *.flac *.ogg *.mp4 *.txt *.md *.vtt *.srt);;"
                "Audio (*.opus *.wav *.mp3 *.m4a *.flac *.ogg *.mp4);;"
                "Transcript (*.txt *.md *.vtt *.srt);;"
                "All files (*.*)"
            ),
        )
        if not path:
            return
        src = Path(path)
        is_transcript = is_transcript_file(src)
        try:
            if is_transcript:
                rid = self.pipeline.import_transcript_file(path)
            else:
                rid = self.pipeline.import_audio_file(path)
        except FileNotFoundError:
            show_in_app_toast("Import failed", "That file no longer exists.")
            return
        except Exception as exc:
            logger.exception("import failed for %r", path)
            kind = "transcript" if is_transcript else "audio"
            show_in_app_toast(
                "Import failed",
                f"Couldn't read that file as {kind}: {exc}",
            )
            return
        if is_transcript:
            show_in_app_toast(
                "Importing transcript",
                f"Summarizing {src.name} — you'll get a notification when it's ready.",
            )
        else:
            show_in_app_toast(
                "Importing audio",
                f"Transcribing {src.name} — you'll get a notification when it's ready.",
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
                action_callback=lambda: self._open_settings_tab("Audio"),
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
            action_callback=lambda: self._open_settings_tab("Audio"),
        )

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
                    action_callback=lambda: self._open_settings_tab("AI"),
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
                action_callback=lambda: self._open_settings_tab("Transcription"),
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
                action_callback=lambda: self._open_settings_tab("AI"),
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

    def _anthropic_key(self) -> str:
        """Read the user's Anthropic key from keyring; '' if unset."""
        import keyring

        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_ANTHROPIC
        try:
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER_ANTHROPIC) or ""
        except Exception:
            return ""

    def _on_chat_send(self, recording_id: int, text: str) -> None:
        """Show user turn immediately + dispatch to a background worker."""
        import threading
        api_key = self._anthropic_key()
        if not _chat_should_send(api_key=api_key, text=text):
            return
        card = getattr(self.summary, "_chat_card", None)
        if card is None:
            return
        card.append_user_message(text)
        card.set_pending(True)
        threading.Thread(
            target=self._chat_worker,
            args=(recording_id, text, api_key),
            daemon=True,
        ).start()

    def _chat_worker(self, recording_id: int, text: str, api_key: str) -> None:
        """Worker thread: call chat.ask; hop result back via QTimer with self.window context."""
        from PySide6.QtCore import QTimer

        from teams_transcriber.chat import (
            ChatApiError,
            ChatAuthError,
            ChatTokenLimitError,
            ask,
        )
        try:
            reply = ask(
                self.db, recording_id, text,
                api_key=api_key, model=self.settings.ai_model,
            )
        except ChatAuthError:
            err = "Anthropic key invalid — reset in Settings → AI."
            QTimer.singleShot(0, self.window,
                              lambda: self._on_chat_failed(recording_id, err))
            return
        except ChatTokenLimitError as exc:
            err = str(exc)
            QTimer.singleShot(0, self.window,
                              lambda: self._on_chat_failed(recording_id, err))
            return
        except ChatApiError as exc:
            err = f"Chat failed: {exc}"
            QTimer.singleShot(0, self.window,
                              lambda: self._on_chat_failed(recording_id, err))
            return
        except Exception as exc:
            logger.exception("chat worker crashed unexpectedly")
            err = f"Chat failed: {exc}"
            QTimer.singleShot(0, self.window,
                              lambda: self._on_chat_failed(recording_id, err))
            return
        QTimer.singleShot(0, self.window,
                          lambda: self._on_chat_done(recording_id, reply))

    def _on_chat_done(self, recording_id: int, reply: str) -> None:
        """Main-thread callback for a successful chat reply."""
        # Only update UI if the user is still on this recording — message is
        # persisted in the DB either way, so it'll show on revisit.
        if self.summary._current_recording_id != recording_id:
            return
        card = getattr(self.summary, "_chat_card", None)
        if card is None:
            return
        card.set_pending(False)
        card.append_assistant_message(reply)

    def _on_chat_failed(self, recording_id: int, err: str) -> None:
        """Main-thread callback for a failed chat call."""
        if self.summary._current_recording_id != recording_id:
            return
        card = getattr(self.summary, "_chat_card", None)
        if card is None:
            return
        card.set_pending(False)
        card.append_error_message(err)

    def _wrike_project_enabled(self) -> bool:
        """True when auto Wrike-project-export is fully configured: a token
        in keyring, the project-export toggle on, and a destination parent
        chosen (Settings -> Integrations -> Wrike)."""
        import keyring

        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE

        integ = self.settings._raw.get("integrations", {})
        if not bool(integ.get("wrike_project_export_enabled", False)):
            return False
        if not integ.get("wrike_parent_id"):
            return False
        try:
            token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
        except Exception:
            return False
        return bool(token)

    def _on_summary_ready_wrike(self, evt) -> None:
        """Auto-push this recording's summary to Wrike as a project, if configured."""
        if not self._wrike_project_enabled():
            return
        self._wrike_export_worker(evt.recording_id)

    def _resolve_wrike_assignees(self, recording_id: int, client) -> dict[int, str | None]:
        """Resolve Wrike contact ids for each action_items_others entry.

        Returns {} when the recording has no action-items-for-others (skips
        the list_contacts round-trip entirely). Gated on
        integrations.wrike_llm_assignee_fallback + a present Anthropic key --
        the LLM pass is a paid extra call, opt-out by default off-key.
        """
        from teams_transcriber.integrations.wrike_assignees import Contact, suggest_assignees
        from teams_transcriber.storage import SummaryRepo

        summary = SummaryRepo(self.db).get(recording_id)
        if summary is None or not summary.action_items_others:
            return {}

        contacts_raw = client.list_contacts()
        contacts = [
            Contact(id=str(c.get("id")),
                    first_name=str(c.get("firstName") or "").strip(),
                    last_name=str(c.get("lastName") or "").strip())
            for c in contacts_raw
        ]
        items = [
            (i, ai.who or "")
            for i, ai in enumerate(summary.action_items_others)
        ]
        anthropic_key = self._anthropic_key()
        llm_enabled = bool(
            self.settings._raw.get("integrations", {}).get("wrike_llm_assignee_fallback", True)
        )
        return suggest_assignees(
            items, contacts,
            meeting_summary=summary.summary,
            api_key=anthropic_key, model=self.settings.ai_model,
            llm_fallback=llm_enabled and bool(anthropic_key),
        )

    def _build_wrike_task_contexts(self, recording_id: int) -> dict[tuple[str, int], str] | None:
        """One batched Claude call generating a transcript-grounded context
        blurb per exported to-do/action-item/follow-up (see
        ``integrations/wrike_task_context.py``). Returns None on ANY failure
        (no API key, network error, ...) -- tasks are then created with no
        description, exactly as before this feature existed.
        """
        from teams_transcriber.integrations.wrike_task_context import build_task_contexts
        from teams_transcriber.storage import SummaryRepo, TranscriptRepo

        try:
            anthropic_key = self._anthropic_key()
            if not anthropic_key:
                return None
            summary = SummaryRepo(self.db).get(recording_id)
            if summary is None:
                return None
            segments = TranscriptRepo(self.db).list_for_recording(recording_id)
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            return build_task_contexts(
                client, summary=summary, segments=segments, model=self.settings.ai_model,
            )
        except Exception:
            logger.exception("wrike task-context generation failed for %d", recording_id)
            return None

    def _wrike_export_worker(self, recording_id: int) -> None:
        """Worker thread: build+run the Wrike project export, then hop a
        toast (success/failure/partial) back to the main thread.

        Guarded by ``self._wrike_exports_in_flight`` -- this method is only
        ever called on the main thread (SummaryReady auto-push, the manual
        Send button, the partial-failure toast's Retry, and the startup
        pending-sync toast are all main-thread call sites), so the add/check
        here and the discard in the worker's finally-hop are never touched
        concurrently.
        """
        import threading

        import keyring
        from PySide6.QtCore import QTimer

        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE

        if recording_id in self._wrike_exports_in_flight:
            show_in_app_toast("Wrike sync", "Wrike sync already running for this meeting.")
            return
        self._wrike_exports_in_flight.add(recording_id)
        show_in_app_toast(
            "Sending to Wrike",
            "Creating the project — this can take a minute.",
        )

        def _worker() -> None:
            from teams_transcriber.integrations.wrike_client import WrikeApiError, WrikeClient
            from teams_transcriber.integrations.wrike_project_export import export_recording
            from teams_transcriber.storage.wrike import WrikeSyncRepo

            try:
                token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
                parent_id = self.settings._raw.get("integrations", {}).get("wrike_parent_id")
                if not token or not parent_id:
                    logger.warning("wrike export skipped for %d: not configured", recording_id)
                    QTimer.singleShot(0, self.window, lambda: show_in_app_toast(
                        "Wrike not configured",
                        "Add your Wrike token and choose a destination in "
                        "Settings → Integrations.",
                        action_label="Open Settings",
                        action_callback=lambda: self._open_settings_tab("Integrations"),
                    ))
                    return
                client = WrikeClient(token=token)
                try:
                    assignees = self._resolve_wrike_assignees(recording_id, client)
                    report = export_recording(
                        self.db, client, recording_id,
                        parent_id=parent_id, assignees=assignees,
                        task_context_provider=lambda: self._build_wrike_task_contexts(recording_id),
                    )
                except WrikeApiError as exc:
                    WrikeSyncRepo(self.db).update(recording_id, status="failed", error_message=str(exc))
                    QTimer.singleShot(0, self.window, lambda e=str(exc): show_in_app_toast("Wrike sync failed", e))
                    return
                except Exception as exc:
                    logger.exception("wrike export crashed for %d", recording_id)
                    WrikeSyncRepo(self.db).update(recording_id, status="failed", error_message=str(exc))
                    QTimer.singleShot(0, self.window, lambda e=str(exc): show_in_app_toast("Wrike sync failed", e))
                    return
                finally:
                    client.close()
                if report.failures:
                    WrikeSyncRepo(self.db).update(recording_id, status="failed",
                                                   error_message="; ".join(report.failures))
                    QTimer.singleShot(0, self.window, lambda: show_in_app_toast(
                        "Wrike sync — partial", f"{len(report.failures)} item(s) failed; will retry.",
                        action_label="Retry", action_callback=lambda: self._wrike_export_worker(recording_id)))
                else:
                    WrikeSyncRepo(self.db).update(recording_id, status="synced")
                    link = report.permalink
                    QTimer.singleShot(0, self.window, lambda: show_in_app_toast(
                        "Synced to Wrike", "Project created.",
                        action_label=("Open in Wrike" if link else None),
                        action_callback=((lambda: __import__("webbrowser").open(link)) if link else None)))
            finally:
                QTimer.singleShot(
                    0, self.window,
                    lambda rid=recording_id: self._wrike_exports_in_flight.discard(rid),
                )

        threading.Thread(target=_worker, daemon=True).start()

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

    def _quit_for_update(self) -> None:
        """Clean shutdown before the installer replaces files on disk."""
        self.hotkeys.stop()
        self.pipeline.shutdown()
        self.db.close()
        self.qapp.exit(0)

    def _start_update_download(self, evt) -> None:
        from teams_transcriber.ui.update_dialog import UpdateDialog
        dlg = UpdateDialog(
            version=evt.version,
            download_url=evt.download_url,
            paths=self.paths,
            parent=self.window,
            quit_callback=self._quit_for_update,
        )
        exec_modal(dlg)

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
        self._transcript_windows = getattr(self, "_transcript_windows", {})
        existing = self._transcript_windows.get(recording_id)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        win = TranscriptWindow(db=self.db, recording_id=recording_id)
        win.closed.connect(
            lambda rid: self._transcript_windows.pop(rid, None)
        )
        self._transcript_windows[recording_id] = win
        win.show()

    def _quit(self) -> None:
        self.hotkeys.stop()
        self.pipeline.shutdown()
        self.db.close()
        self.qapp.quit()

    def run(self) -> int:
        return int(self.qapp.exec())


def main() -> int:
    from teams_transcriber.logging_config import configure_logging
    configure_logging()
    app = App()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
