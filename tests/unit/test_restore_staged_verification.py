"""Test that restore verifies the staged copy it installs (D349)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import LATEST_VERSION, MIGRATIONS
from infiniteprep.db.migrations import backup as backup_module
from infiniteprep.db.migrations.backup import (
    BackupVerificationError,
    restore_pre_migration_backup,
)
from tests.fixtures.sqlite_version import read_user_version

LIBRARY_NAME = "infiniteprep.sqlite3"
RESTORE_TIME = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)
STAGED_SUFFIX = ".restore.tmp"


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """Leave a newer library with a version-0 backup that holds one source group."""
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
    with closing(sqlite3.connect(db_path)) as newer, newer:
        newer.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")
    return db_path


def test_staged_copy_that_differs_from_the_record_is_never_installed(
    library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backup swapped after its check fails the staged-copy check. The library stays."""
    real_copyfile = backup_module.shutil.copyfile

    def copy_then_empty_table(source: str | Path, target: str | Path) -> None:
        """Copy faithfully, then change the staged rows, as a swap after the check does."""
        real_copyfile(source, target)
        with closing(sqlite3.connect(target)) as staged, staged:
            staged.execute("DELETE FROM source_groups")

    monkeypatch.setattr(backup_module.shutil, "copyfile", copy_then_empty_table)

    with pytest.raises(BackupVerificationError):
        restore_pre_migration_backup(library, now=RESTORE_TIME)

    assert read_user_version(library) == LATEST_VERSION + 1
    assert list(library.parent.glob(f"{LIBRARY_NAME}.newer-*")) == []
    assert not library.with_name(library.name + STAGED_SUFFIX).exists()
