"""Schema v2: add `manual_notes` column to the `recordings` table.

`manual_notes` is HTML-formatted text the user adds during/after a meeting via the
notes editor. Included in the AI summarization prompt and rendered in the summary
pane.
"""

from __future__ import annotations

import sqlite3

from teams_transcriber.storage.migrations import Migration


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE recordings ADD COLUMN manual_notes TEXT")


SCHEMA_V2 = Migration(version=2, name="add manual_notes", apply=_apply)
