"""Read the schema version of a library file for the restore and migration tests."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


def read_user_version(db_path: Path) -> int:
    """Return PRAGMA user_version through a separate connection that closes afterwards."""
    with closing(sqlite3.connect(db_path)) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
