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
from teams_transcriber.ui.scrim import exec_modal
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


def _chat_should_send(*, api_key: str, text: str) -> bool:
    return bool(api_key) and bool(text.strip())


def _wrike_lru_push(items: list[str], value: str, *, cap: int) -> list[str]:
    rest = [i for i in items if i != value]
    return ([value] + rest)[:cap]


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


def _wrike_close_loop_changes(
    rows: list,                  # list[WrikeTaskRow]
    todo_states: dict[int, bool],
) -> list[tuple]:                # list[(WrikeTaskRow, new_done)]
    """Return only the (row, new_done) pairs whose state changed since
    last sync. Filters out the 'other' kind — action-items-for-others
    aren't toggleable in the app, only my-todos are."""
    out: list[tuple] = []
    for r in rows:
        if r.kind != "my":
            continue
        new_done = bool(todo_states.get(r.todo_index, False))
        if new_done != r.last_synced_done:
            out.append((r, new_done))
    return out


def _wrike_open_planner_kwargs(
    db, recording_id, *, folders, recent_folder_ids, contacts, assignee_suggestions,
) -> dict:
    """Assemble WrikeSyncPlanner kwargs. Pure, so the threaded App method stays
    testable without a QApplication. Converts persisted wrike_tasks DB kinds
    back to SyncKind for already_synced_keys, because the planner is
    SyncKind-native (a stored 'my' must lock the planner's 'my_todo' row)."""
    from teams_transcriber.integrations.wrike_items import recording_to_sync_items
    from teams_transcriber.integrations.wrike_sync import db_kind_to_sync_kind
    from teams_transcriber.storage.wrike import WrikeTaskRepo

    items = recording_to_sync_items(db, recording_id)
    already = {
        (db_kind_to_sync_kind(r.kind), r.todo_index)
        for r in WrikeTaskRepo(db).list_for_recording(recording_id)
    }
    return {
        "items": items,
        "folders": folders,
        "recent_folder_ids": recent_folder_ids,
        "contacts": contacts,
        "assignee_suggestions": assignee_suggestions,
        "already_synced_keys": already,
    }


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
                    action_label="Pick folder",
                    action_callback=lambda r=rid: self._wrike_open_picker(r),
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
            wrike_available=self._wrike_is_configured,
            anthropic_key_getter=self._anthropic_key,
        )
        self.summary.export_requested.connect(self._export_summary)
        self.summary.delete_requested.connect(self._delete_recording)
        self.summary.notes_requested.connect(self._open_workspace)
        self.summary.retry_requested.connect(self._retry_recording)
        self.summary.transcript_requested.connect(self._show_transcript)
        self.summary.todo_state_changed.connect(self._on_todo_state_changed)
        self.summary.wrike_sync_requested.connect(self._wrike_open_picker)
        self.summary.chat_send_requested.connect(self._on_chat_send)
        from teams_transcriber.ui.window_state import (
            restore_splitter_state, save_splitter_state,
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

    def _on_todo_state_changed(self, rid: int) -> None:
        self._refresh_history(query=self.search.input.text() or None)
        self.master_todos.reload()
        self._wrike_close_loop_sync(rid)

    def _on_master_todo_toggled(self, recording_id: int) -> None:
        """Master-view toggle: same close-loop as the summary pane's checkbox.
        No master_todos.reload() here — the toggled checkbox is the sender and
        reload would delete it mid-signal; the view already shows the new state."""
        self._refresh_history(query=self.search.input.text() or None)
        self._wrike_close_loop_sync(recording_id)

    def _wrike_close_loop_sync(self, recording_id: int) -> None:
        """Compute which my-todos changed and dispatch a worker to push them."""
        import keyring
        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE
        from teams_transcriber.storage import TodoStateRepo
        from teams_transcriber.storage.wrike import WrikeTaskRepo

        token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
        enabled = bool(
            self.settings._raw.get("integrations", {}).get("wrike_enabled", False)
        )
        if not (enabled and token):
            return
        rows = WrikeTaskRepo(self.db).list_for_recording(recording_id)
        if not rows:
            return
        todo_states = {
            s.todo_index: s.done
            for s in TodoStateRepo(self.db).list_for_recording(recording_id)
        }
        changes = _wrike_close_loop_changes(rows, todo_states)
        if not changes:
            return
        threading.Thread(
            target=self._wrike_apply_close_loop,
            args=(recording_id, changes, token),
            daemon=True,
        ).start()

    def _wrike_apply_close_loop(
        self, recording_id: int, changes: list, token: str,
    ) -> None:
        """Background-thread worker: call Wrike API per changed todo."""
        from teams_transcriber.integrations.wrike_client import (
            WrikeClient, WrikeApiError,
        )
        from teams_transcriber.storage.wrike import WrikeTaskRepo

        client = WrikeClient(token=token)
        repo = WrikeTaskRepo(self.db)
        try:
            for row, new_done in changes:
                try:
                    client.complete_task(row.wrike_task_id, done=new_done)
                    repo.set_last_synced_done(
                        recording_id, row.kind, row.todo_index, new_done,
                    )
                except WrikeApiError as exc:
                    logger.warning(
                        "Wrike close-loop failed for %s: %s",
                        row.wrike_task_id, exc,
                    )
        finally:
            client.close()

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
        self._open_settings_tab(None)

    def _open_settings_tab(self, tab: str | None) -> None:
        """Open Settings, optionally jumping to a named tab."""
        from PySide6.QtWidgets import QTabWidget
        from teams_transcriber.ui.scrim import exec_modal
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
            ChatApiError, ChatAuthError, ChatTokenLimitError, ask,
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

    def _wrike_is_configured(self) -> bool:
        """True when Wrike sync is enabled AND a token is stored in keyring.

        Used by SummaryPane to decide whether to show the "Send to Wrike"
        button, and as a guard before opening the picker.
        """
        import keyring
        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE
        enabled = bool(
            self.settings._raw.get("integrations", {}).get("wrike_enabled", False)
        )
        if not enabled:
            return False
        try:
            token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
        except Exception:
            return False
        return bool(token)

    def _on_summary_ready_wrike(self, evt) -> None:
        """Offer to sync this summary's items to Wrike via a toast + planner."""
        import keyring
        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE
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
        from teams_transcriber.integrations.wrike_items import recording_to_sync_items
        items = recording_to_sync_items(self.db, evt.recording_id)
        if not items:
            return
        n = len(items)
        WrikeSyncRepo(self.db).upsert(evt.recording_id, status="pending")
        rid = evt.recording_id
        show_in_app_toast(
            "Send to Wrike",
            f"{n} item{'s' if n != 1 else ''} ready — review and send.",
            action_label="Review",
            action_callback=lambda: self._wrike_open_planner(rid),
        )

    def _wrike_open_picker(self, recording_id: int) -> None:
        """Kept as the connected-signal entry point; delegates to the planner."""
        self._wrike_open_planner(recording_id)

    def _wrike_open_planner(self, recording_id: int) -> None:
        """Fetch folders + contacts in a worker, resolve assignees, show planner."""
        import keyring
        import threading
        from PySide6.QtCore import QTimer
        from teams_transcriber.config import KEYRING_SERVICE, KEYRING_USER_WRIKE
        from teams_transcriber.integrations.wrike_assignees import Contact, suggest_assignees
        from teams_transcriber.integrations.wrike_client import WrikeApiError, WrikeClient
        from teams_transcriber.integrations.wrike_items import recording_to_sync_items
        from teams_transcriber.storage import SummaryRepo

        token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_WRIKE) or ""
        if not token:
            show_in_app_toast("Wrike not configured", "Add a token in Settings → Integrations.")
            return
        items = recording_to_sync_items(self.db, recording_id)
        if not items:
            show_in_app_toast("Nothing to send", "This recording has no syncable items.")
            return

        summary = SummaryRepo(self.db).get(recording_id)
        meeting_summary_text = (summary.summary if summary else "") or ""
        anthropic_key = self._anthropic_key()
        llm_enabled = bool(
            self.settings._raw.get("integrations", {}).get("wrike_llm_assignee_fallback", True)
        )
        model = self.settings.ai_model

        def _worker() -> None:
            # PySide6 gotcha: the 3-arg singleShot(0, qobj, callable) binds the
            # timer to qobj's (main-GUI) thread; the 2-arg form would create the
            # timer on this worker thread, which has no event loop.
            client = WrikeClient(token=token)
            try:
                folders = client.list_folders()
                need_contacts = any(it.kind == "action_other" for it in items)
                contacts_raw = client.list_contacts() if need_contacts else []
            except WrikeApiError as exc:
                QTimer.singleShot(0, self.window, lambda e=str(exc): self._wrike_picker_load_failed(recording_id, e))
                return
            except Exception as exc:
                logger.exception("Wrike planner preload failed")
                QTimer.singleShot(0, self.window, lambda e=str(exc): self._wrike_picker_load_failed(recording_id, e))
                return
            finally:
                client.close()

            # Resolve phase: assignee suggestion can do an LLM call. Guard it so
            # an unexpected raise surfaces a toast (and marks the sync failed)
            # instead of leaving the row stuck at "pending" with no feedback.
            try:
                contacts = [
                    Contact(id=str(c.get("id")),
                            first_name=str(c.get("firstName") or "").strip(),
                            last_name=str(c.get("lastName") or "").strip())
                    for c in contacts_raw
                ]
                action_other_items = [
                    (i, it.suggested_who or "")
                    for i, it in enumerate(items) if it.kind == "action_other"
                ]
                suggestions = suggest_assignees(
                    action_other_items, contacts,
                    meeting_summary=meeting_summary_text,
                    api_key=anthropic_key, model=model,
                    llm_fallback=llm_enabled and bool(anthropic_key),
                ) if action_other_items else {}
            except Exception as exc:
                logger.exception("Wrike assignee resolution failed")
                QTimer.singleShot(0, self.window, lambda e=str(exc): self._wrike_picker_load_failed(recording_id, e))
                return

            QTimer.singleShot(0, self.window, lambda: self._wrike_planner_show(
                recording_id, folders, contacts, suggestions, token))

        threading.Thread(target=_worker, daemon=True).start()

    def _wrike_picker_load_failed(self, recording_id: int, msg: str) -> None:
        from teams_transcriber.storage.wrike import WrikeSyncRepo
        show_in_app_toast("Wrike error", msg)
        WrikeSyncRepo(self.db).update(
            recording_id, status="failed", error_message=msg,
        )

    def _wrike_planner_show(
        self, recording_id, folders, contacts, assignee_suggestions, token,
    ) -> None:
        import threading

        from teams_transcriber.config import save_settings
        from teams_transcriber.ui.wrike_sync_planner import WrikeSyncPlanner

        recent_ids = list(
            self.settings._raw.get("integrations", {}).get("wrike_recent_folder_ids", []) or []
        )
        kwargs = _wrike_open_planner_kwargs(
            self.db, recording_id, folders=folders, recent_folder_ids=recent_ids,
            contacts=contacts, assignee_suggestions=assignee_suggestions,
        )
        dlg = WrikeSyncPlanner(parent=self.window, **kwargs)
        if exec_modal(dlg) != dlg.DialogCode.Accepted:
            return
        plan = dlg.build_plan()
        if not plan:
            return
        primary_folder = max(
            (r.folder_id for r in plan),
            key=lambda fid: sum(1 for r in plan if r.folder_id == fid),
        )
        new_recent = _wrike_lru_push(recent_ids, primary_folder, cap=5)
        self.settings._raw.setdefault("integrations", {})["wrike_recent_folder_ids"] = new_recent
        save_settings(self.paths, self.settings)
        threading.Thread(
            target=self._wrike_run_plan,
            args=(recording_id, plan, primary_folder, token),
            daemon=True,
        ).start()

    def _wrike_run_plan(self, recording_id, plan, primary_folder, token) -> None:
        """Background-thread sync. Updates wrike_sync status + toasts the result.

        Toasts are scheduled on the main thread via the 3-arg QTimer.singleShot
        with self.window as context — show_in_app_toast creates QWidgets and
        must not run on a worker thread.
        """
        from PySide6.QtCore import QTimer
        from teams_transcriber.integrations.wrike_client import WrikeApiError, WrikeClient
        from teams_transcriber.integrations.wrike_sync import sync_items
        from teams_transcriber.storage.wrike import WrikeSyncRepo

        client = WrikeClient(token=token)
        try:
            report = sync_items(self.db, recording_id, plan, client=client)
            WrikeSyncRepo(self.db).update(
                recording_id,
                status="synced" if not report.failures else "failed",
                folder_id=primary_folder,
                error_message=(None if not report.failures else f"{len(report.failures)} item(s) failed"),
            )
            bits = []
            if report.created_tasks:
                bits.append(f"{report.created_tasks} task{'s' if report.created_tasks != 1 else ''}")
            if report.created_comments:
                bits.append(f"{report.created_comments} comment{'s' if report.created_comments != 1 else ''}")
            if report.skipped_already_synced:
                bits.append(f"{report.skipped_already_synced} already synced")
            body = ", ".join(bits) or "Nothing to do."
            title = "Synced to Wrike"
            if report.failures:
                title = "Wrike sync — partial failure"
                body = f"{body} · {len(report.failures)} failed"
            QTimer.singleShot(0, self.window, lambda: show_in_app_toast(title, body))
        except WrikeApiError as exc:
            WrikeSyncRepo(self.db).update(recording_id, status="failed", error_message=str(exc))
            err = str(exc)
            QTimer.singleShot(0, self.window, lambda: show_in_app_toast("Wrike sync failed", err))
        except Exception as exc:
            # sync_items absorbs per-row failures; a raise here is a non-API
            # error (e.g. DB). Don't let it die silently on the worker thread.
            logger.exception("Wrike sync_items failed unexpectedly")
            WrikeSyncRepo(self.db).update(recording_id, status="failed", error_message=str(exc))
            err = str(exc)
            QTimer.singleShot(0, self.window, lambda: show_in_app_toast("Wrike sync failed", err))
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = App()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
