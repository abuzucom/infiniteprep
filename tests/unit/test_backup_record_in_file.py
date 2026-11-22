"""Test that the backup carries its own record. One rename publishes both (D349)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import LATEST_VERSION, MIGRATIONS
from infiniteprep.db.migrations.backup import (
    RECORD_TABLE,
    BackupVerificationError,
    backup_path_for,
    read_backup_record,
    restore_pre_migration_backup,
    sidecar_path_for,
    usable_backup_path,
)

LIBRARY_NAME = "infiniteprep.sqlite3"
RESTORE_TIME = datetime(2026, 11, 15, 13, 0, 0, tzinfo=UTC)


def _table_names(db_path: Path) -> set[str]:
    """Return every table name in the database at db_path."""
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """Leave a migrated library with a version-0 backup that holds one source group."""
    db_path = tmp_path / LIBRARY_NAME
    with closing(sqlite3.connect(db_path)) as conn, conn:
        MIGRATIONS[0].apply(conn)
        conn.execute(
            "INSERT INTO source_groups (id, user_label, grouping_basis, original_folder) "
            "VALUES (?, ?, ?, ?)",
            ("group-1", "Crate", "folder", "/music/crate"),
        )
        conn.execute("PRAGMA user_version = 0")
    DatabaseManager(db_path).close()
    return db_path


def test_backup_holds_its_record_and_no_sidecar_exists(library: Path) -> None:
    """The record travels inside the backup file. No second file needs publishing."""
    expected_counts = dict.fromkeys(MIGRATIONS[0].row_count_queries, 0)
    expected_counts["source_groups"] = 1

    assert read_backup_record(library) == (0, expected_counts)
    assert RECORD_TABLE in _table_names(backup_path_for(library))
    assert not sidecar_path_for(library).exists()


def test_restored_library_carries_no_record_table(library: Path) -> None:
    """Restore removes the record from the copy it installs."""
    with closing(sqlite3.connect(library)) as conn, conn:
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")

    restore_pre_migration_backup(library, now=RESTORE_TIME)

    assert RECORD_TABLE not in _table_names(library)
    assert "source_groups" in _table_names(library)


def test_backup_without_record_is_ignored(library: Path) -> None:
    """A backup whose record is missing fails both the offer check and restore."""
    with closing(sqlite3.connect(backup_path_for(library))) as conn, conn:
        conn.execute(f"DROP TABLE {RECORD_TABLE}")
    with closing(sqlite3.connect(library)) as conn, conn:
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")

    assert usable_backup_path(library, LATEST_VERSION) is None
    with pytest.raises(BackupVerificationError):
        restore_pre_migration_backup(library, now=RESTORE_TIME)
