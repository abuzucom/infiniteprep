"""Storage for imported DJ libraries and their path mappings (D349).

The tables arrive through a numbered migration. The migration runner takes a
verified backup before each step, so the change follows the same rule as every
other schema change.
"""

import sqlite3
from typing import Final

VERSION: Final[int] = 4
ROW_COUNT_QUERIES: Final[dict[str, str]] = {
    "imported_libraries": "SELECT COUNT(*) FROM imported_libraries",
    "library_path_mappings": "SELECT COUNT(*) FROM library_path_mappings",
}

STATEMENTS: Final = (
    """CREATE TABLE imported_libraries (
        id TEXT PRIMARY KEY NOT NULL,
        format_id TEXT NOT NULL,
        source_path TEXT NOT NULL,
        detected_version TEXT NOT NULL DEFAULT '',
        imported_at TEXT NOT NULL
    )""",
    "CREATE INDEX imported_libraries_format ON imported_libraries(format_id)",
    """CREATE TABLE library_path_mappings (
        id TEXT PRIMARY KEY NOT NULL,
        library_id TEXT NOT NULL REFERENCES imported_libraries(id) ON DELETE CASCADE,
        source_prefix TEXT NOT NULL,
        target_prefix TEXT NOT NULL,
        root_path TEXT NOT NULL
    )""",
    "CREATE INDEX library_path_mappings_library ON library_path_mappings(library_id)",
)


def apply(conn: sqlite3.Connection) -> None:
    """Run the library import DDL inside the migration runner's transaction."""
    for statement in STATEMENTS:
        conn.execute(statement)
