"""argparse-based CLI for the headless pipeline."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from teams_transcriber.config import load_settings
from teams_transcriber.events import EventBus
from teams_transcriber.meeting_watcher import MeetingWatcher, enumerate_windows
from teams_transcriber.paths import AppPaths
from teams_transcriber.pipeline import Pipeline
from teams_transcriber.storage import RecordingRepo, build_database
from teams_transcriber.summarizer import Summarizer
from teams_transcriber.transcriber import Transcriber

logger = logging.getLogger(__name__)


def _build_pipeline(paths: AppPaths, *, with_watcher: bool) -> Pipeline:
    settings = load_settings(paths)
    db = build_database(paths.db_path)
    db.initialize()
    bus = EventBus()

    def _audio_factory() -> Any:
        from teams_transcriber.audio.source import RealAudioSource
        return RealAudioSource.from_settings(settings)

    watcher = None
    if with_watcher:
        watcher = MeetingWatcher(
            bus=bus,
            current_windows=enumerate_windows,
            title_patterns=settings.detection_title_patterns,
            debounce_polls=settings.detection_debounce_polls,
            poll_interval_ms=settings.detection_poll_interval_ms,
        )

    return Pipeline(
        bus=bus, db=db, paths=paths, settings=settings,
        audio_source_factory=_audio_factory,
        meeting_watcher=watcher,
        transcriber=Transcriber(bus=bus, db=db, settings=settings),
        summarizer=Summarizer(bus=bus, db=db, settings=settings),
    )


def _cmd_serve(args: argparse.Namespace) -> int:
    paths = AppPaths()
    paths.ensure_dirs()
    pipeline = _build_pipeline(paths, with_watcher=True)
    pipeline.serve()

    stop_event = threading.Event()

    def _handle_signal(_sig: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    print("Watching for Teams meetings. Ctrl-C to stop.", file=sys.stderr)
    stop_event.wait()
    pipeline.shutdown()
    return 0


def _cmd_retry_summary(args: argparse.Namespace) -> int:
    paths = AppPaths()
    pipeline = _build_pipeline(paths, with_watcher=False)
    api_key = load_settings(paths).anthropic_api_key()
    pipeline.retry_summary(args.recording_id, api_key=api_key)
    # retry_summary is async on the executor now; shutdown() joins it so the
    # CLI blocks until the summarize call actually finishes.
    pipeline.shutdown()
    return 0


def _cmd_phone_sync(args: argparse.Namespace) -> int:
    """One sync cycle against a plain folder (LocalDirTransport).

    Useful headlessly and with folder-sync tools today; the UI/MTP flow in
    Phase 2 reuses run_sync with a different transport.
    """
    from datetime import UTC, datetime

    from teams_transcriber.phone_sync.sync import run_sync
    from teams_transcriber.phone_sync.transport import LocalDirTransport

    paths = AppPaths()
    paths.ensure_dirs()
    pipeline = _build_pipeline(paths, with_watcher=False)
    try:
        report = run_sync(
            pipeline.db,
            LocalDirTransport(Path(args.folder)),
            import_recording=pipeline.import_phone_recording,
            now_iso=datetime.now(UTC).isoformat(),
        )
    finally:
        pipeline.shutdown()   # waits for queued transcribe/summarize work
    print(f"Imported {len(report.imported)}, skipped {report.skipped_known} known, "
          f"toggles applied {report.toggles_applied} "
          f"({report.toggles_skipped_stale} stale), "
          f"failures {len(report.failures)}")
    for name, why in report.failures:
        print(f"  FAILED {name}: {why}")
    return 1 if report.failures else 0


def _cmd_ui(args: argparse.Namespace) -> int:
    del args
    from teams_transcriber.ui.app import main as ui_main
    return ui_main()


def _cmd_smoke_test(_args: argparse.Namespace) -> int:
    """Import the full pipeline stack and exit 0.

    Used by the build script to verify the frozen .exe loads all native
    dependencies (PyAV, ctranslate2, soundcard, CUDA wheels, Qt) without
    actually launching the UI.
    """
    from teams_transcriber.audio.opus_writer import OpusWriter  # noqa: F401
    from teams_transcriber.audio.splitter import split_channels_to_wav  # noqa: F401
    from teams_transcriber.pipeline import Pipeline  # noqa: F401
    from teams_transcriber.summarizer import Summarizer  # noqa: F401
    from teams_transcriber.transcriber import Transcriber  # noqa: F401
    from teams_transcriber.ui.app import App  # noqa: F401

    print("smoke-test ok", file=sys.stderr)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    paths = AppPaths()
    db = build_database(paths.db_path)
    db.initialize()
    try:
        recs = RecordingRepo(db).list_recent(limit=args.limit)
        for r in recs:
            print(f"#{r.id:>4}  {r.started_at}  [{r.status.value:>14}]  "
                  f"{r.display_title or r.detected_title or '(untitled)'}")
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    parser = argparse.ArgumentParser(prog="teams-transcriber")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="Run the background pipeline.")
    p_serve.set_defaults(func=_cmd_serve)

    p_retry = sub.add_parser("retry-summary", help="Retry a failed summary by recording id.")
    p_retry.add_argument("recording_id", type=int)
    p_retry.set_defaults(func=_cmd_retry_summary)

    p_list = sub.add_parser("list", help="List recent recordings.")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=_cmd_list)

    p_ui = sub.add_parser("ui", help="Launch the desktop UI.")
    p_ui.set_defaults(func=_cmd_ui)

    p_smoke = sub.add_parser("smoke-test", help="Boot all imports and exit 0 (build verification).")
    p_smoke.set_defaults(func=_cmd_smoke_test)

    p_phone = sub.add_parser("phone-sync", help="Sync a phone folder (recordings in, library out)")
    p_phone.add_argument("folder", help="Path to the TeamsTranscriber folder (outbox/library/sync)")
    p_phone.set_defaults(func=_cmd_phone_sync)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
