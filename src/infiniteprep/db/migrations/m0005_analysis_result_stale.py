"""Mark analysis results from an older analyzer version as stale.

A result carries the analyzer version that wrote it. A version change leaves the older
result in the table. The stale flag records that the result no longer states the current
BPM or key, so the app can show it as outdated and leave it out of the proposals.
"""

import sqlite3
from typing import Final

VERSION: Final[int] = 5
# The step adds no table, so backup verification needs no new count query.
ROW_COUNT_QUERIES: Final[dict[str, str]] = {}

STATEMENTS: Final = (
    # SQLite accepts a NOT NULL column on ALTER TABLE only with a default. Every row that
    # exists now was written by the running analyzer version, so the default of zero marks
    # it current.
    "ALTER TABLE analysis_results ADD COLUMN stale INTEGER NOT NULL DEFAULT 0",
)


def apply(conn: sqlite3.Connection) -> None:
    """Run the column DDL inside the migration runner's transaction."""
    for statement in STATEMENTS:
        conn.execute(statement)
