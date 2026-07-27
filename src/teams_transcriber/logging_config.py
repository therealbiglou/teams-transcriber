"""Central logging setup shared by the UI and headless CLI entry points.

Console-only ``logging.basicConfig`` is invisible in a windowed/installed
build: ``pythonw``/PyInstaller ``.exe`` runs have no console, so nothing was
ever written anywhere -- the ``logs/`` directory the app creates stayed
permanently empty, and a stalled/failed background job (e.g. a Wrike export)
was undiagnosable after the fact. This adds a rotating file handler under
``AppPaths().logs_dir`` alongside the existing console handler.
"""

from __future__ import annotations

import logging
import logging.handlers

from teams_transcriber.paths import AppPaths

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_LOG_FILENAME = "teams_transcriber.log"
_MAX_BYTES = 2_000_000
_BACKUP_COUNT = 3

_configured = False


def configure_logging(paths: AppPaths | None = None) -> None:
    """Configure the root logger once: console (dev visibility) + rotating
    file (diagnostics for windowed/installed runs).

    Idempotent and safe to call from both ``cli.main`` and ``ui.app.main``
    even when both execute in the same process (``teams-transcriber ui``
    dispatches from the CLI's ``main`` into the UI's ``main``). Never
    raises -- if the log file can't be opened (permissions, disk full,
    ...) this falls back to console-only logging.
    """
    global _configured
    if _configured:
        return
    _configured = True

    # Console handler, via basicConfig so it keeps basicConfig's own
    # guard (no-op if the root logger already has handlers) instead of
    # unconditionally adding a StreamHandler bound to whatever `sys.stderr`
    # happens to be at this moment. That guard matters under pytest: the
    # test runner pre-installs its own handlers on the root logger (for
    # per-test log capture), and those swap `sys.stderr`/close their capture
    # object between tests -- a StreamHandler added here that ignored the
    # guard would eventually try to write to a closed stream from a
    # previous, unrelated test.
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    formatter = logging.Formatter(_LOG_FORMAT)

    try:
        p = paths or AppPaths()
        p.logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            p.logs_dir / _LOG_FILENAME,
            maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except Exception:
        root.warning("failed to set up file logging; continuing with console only",
                      exc_info=True)
