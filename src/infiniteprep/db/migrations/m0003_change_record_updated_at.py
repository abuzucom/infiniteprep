"""Add the last-update time to change journal records.

The timestamp column keeps the first write of an operation. updated_at holds the latest
status change. Existing rows receive their timestamp as the starting value.
"""

import sqlite3
from typing import Final

VERSION: Final[int] = 3
# The step creates no table, so backup verification needs no new count query.
ROW_COUNT_QUERIES: Final[dict[str, str]] = {}

STATEMENTS: Final = (
    # SQLite adds a NOT NULL column only with a default. The column stays nullable, and the
    # repository writes a value on every insert and update.
    "ALTER TABLE change_records ADD COLUMN updated_at TEXT",
    "UPDATE change_records SET updated_at = timestamp",
)


def apply(conn: sqlite3.Connection) -> None:
    """Run the column DDL and the backfill inside the migration runner's transaction."""
    for statement in STATEMENTS:
        conn.execute(statement)
