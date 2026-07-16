"""Storage layer: SQLite-backed persistence for recordings, transcripts, summaries, todos.

Usage:
    from teams_transcriber.paths import AppPaths
    from teams_transcriber.storage import build_database, RecordingRepo, TranscriptRepo

    paths = AppPaths()
    paths.ensure_dirs()
    db = build_database(paths.db_path)
    db.initialize()
    recordings = RecordingRepo(db)
    ...
"""

from pathlib import Path

from teams_transcriber.storage.db import Database
from teams_transcriber.storage.migrations import Migration, MigrationRunner
from teams_transcriber.storage.models import (
    ActionItemOther,
    Channel,
    Recording,
    RecordingSource,
    RecordingStatus,
    Summary,
    TodoItem,
    TodoState,
    TranscriptSegment,
)
from teams_transcriber.storage.phone import PhoneImportRepo
from teams_transcriber.storage.recordings import RecordingRepo
from teams_transcriber.storage.retention import AudioRetentionPruner, PruneReport
from teams_transcriber.storage.chat import ChatMessage, ChatRepo
from teams_transcriber.storage.schema_v1 import SCHEMA_V1
from teams_transcriber.storage.schema_v2 import SCHEMA_V2
from teams_transcriber.storage.schema_v3 import SCHEMA_V3
from teams_transcriber.storage.schema_v4 import SCHEMA_V4
from teams_transcriber.storage.schema_v5 import SCHEMA_V5
from teams_transcriber.storage.schema_v6 import SCHEMA_V6
from teams_transcriber.storage.schema_v7 import SCHEMA_V7
from teams_transcriber.storage.summaries import SummaryRepo
from teams_transcriber.storage.todos import TodoStateRepo
from teams_transcriber.storage.transcripts import SearchHit, TranscriptRepo
from teams_transcriber.storage.wrike import WrikeSyncRepo, WrikeSyncRow, WrikeTaskRepo, WrikeTaskRow

ALL_MIGRATIONS: tuple[Migration, ...] = (
    SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5, SCHEMA_V6, SCHEMA_V7,
)


def build_database(path: Path | str) -> Database:
    """Construct a Database with the canonical migration set applied.

    The caller is responsible for calling `db.initialize()` after construction.
    """
    return Database(Path(path), migrations=ALL_MIGRATIONS)


__all__ = [
    "ALL_MIGRATIONS",
    "ChatMessage",
    "ChatRepo",
    "PhoneImportRepo",
    "SCHEMA_V1",
    "SCHEMA_V5",
    "SCHEMA_V6",
    "SCHEMA_V7",
    "ActionItemOther",
    "AudioRetentionPruner",
    "Channel",
    "Database",
    "Migration",
    "MigrationRunner",
    "PruneReport",
    "Recording",
    "RecordingRepo",
    "RecordingSource",
    "RecordingStatus",
    "SearchHit",
    "Summary",
    "SummaryRepo",
    "TodoItem",
    "TodoState",
    "TodoStateRepo",
    "TranscriptRepo",
    "TranscriptSegment",
    "WrikeSyncRepo",
    "WrikeSyncRow",
    "WrikeTaskRepo",
    "WrikeTaskRow",
    "build_database",
]
