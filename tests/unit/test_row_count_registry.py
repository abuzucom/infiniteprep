"""Test that backup verification counts rows in every application table (D349)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import ROW_COUNT_QUERIES
from infiniteprep.db.migrations.backup import table_row_counts


def test_registry_covers_every_table_of_a_migrated_library(tmp_path: Path) -> None:
    """Each table in a fully migrated library has a literal count query."""
    library = tmp_path / "infiniteprep.sqlite3"
    with DatabaseManager(library):
        pass
    with sqlite3.connect(library) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    assert tables == set(ROW_COUNT_QUERIES)


def test_counts_skip_tables_the_library_lacks(tmp_path: Path) -> None:
    """A library from before a table existed reports counts only for the tables it holds."""
    with sqlite3.connect(tmp_path / "old.sqlite3") as conn:
        conn.execute("CREATE TABLE source_groups (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO source_groups (id) VALUES (?)", ("group-1",))

        assert table_row_counts(conn) == {"source_groups": 1}
