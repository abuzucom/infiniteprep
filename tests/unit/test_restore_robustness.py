"""Test that restore checks row counts and leaves no split library on failure (D349)."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import LATEST_VERSION, MIGRATIONS
from infiniteprep.db.migrations import backup as backup_module
from infiniteprep.db.migrations.backup import (
    BackupVerificationError,
    backup_path_for,
    newer_copy_path_for,
    restore_pre_migration_backup,
)

LIBRARY_NAME = "infiniteprep.sqlite3"
RESTORE_TIME = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)
SIDE_FILE_SUFFIXES = ("-wal", "-shm")
SIDE_FILE_BYTES = b"side file"
STAGED_SUFFIX = ".restore.tmp"


def _version_of(db_path: Path) -> int:
    """Read user_version through a separate connection."""
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """Leave a newer library with a version-0 backup that holds one source group."""
    db_path = tmp_path / LIBRARY_NAME
    conn = sqlite3.connect(db_path)
    try:
        MIGRATIONS[0].apply(conn)
        conn.execute(
            "INSERT INTO source_groups (id, user_label, grouping_basis, original_folder) "
            "VALUES (?, ?, ?, ?)",
            ("group-1", "Crate", "folder", "/music/crate"),
        )
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        conn.close()
    DatabaseManager(db_path).close()
    with sqlite3.connect(db_path) as newer:
        newer.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")
    return db_path


def _add_side_files(db_path: Path) -> None:
    """Leave -wal and -shm files beside the library, as after a crash of the newer app."""
    for suffix in SIDE_FILE_SUFFIXES:
        Path(f"{db_path}{suffix}").write_bytes(SIDE_FILE_BYTES)


def _assert_library_untouched(db_path: Path) -> None:
    """Assert that the newer library and side files keep the original names. No kept copy exists.

    The side-file checks run before any open. An open rewrites the side files.
    """
    for suffix in SIDE_FILE_SUFFIXES:
        assert Path(f"{db_path}{suffix}").read_bytes() == SIDE_FILE_BYTES
    assert list(db_path.parent.glob(f"{LIBRARY_NAME}.newer-*")) == []
    assert not db_path.with_name(db_path.name + STAGED_SUFFIX).exists()
    assert _version_of(db_path) == LATEST_VERSION + 1


def test_restore_rejects_backup_with_changed_rows(library: Path) -> None:
    """A backup edited after creation fails the row counts recorded at backup time."""
    with sqlite3.connect(backup_path_for(library)) as conn:
        conn.execute("DELETE FROM source_groups")

    with pytest.raises(BackupVerificationError):
        restore_pre_migration_backup(library, now=RESTORE_TIME)

    assert _version_of(library) == LATEST_VERSION + 1


def test_failed_library_rename_puts_side_files_back(
    library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename that fails partway moves every file back. A later restore succeeds."""
    _add_side_files(library)
    real_replace = os.replace
    blocked_source = Path(f"{library}-shm")

    def replace_except_shm(source: str | Path, target: str | Path) -> None:
        """Fail the move of the -shm file, as a locked file on Windows does."""
        if Path(source) == blocked_source:
            raise PermissionError(f"{source} is in use")
        real_replace(source, target)

    monkeypatch.setattr(backup_module.os, "replace", replace_except_shm)

    with pytest.raises(PermissionError):
        restore_pre_migration_backup(library, now=RESTORE_TIME)

    _assert_library_untouched(library)
    monkeypatch.setattr(backup_module.os, "replace", real_replace)
    kept = restore_pre_migration_backup(library, now=RESTORE_TIME)
    assert kept == newer_copy_path_for(library, RESTORE_TIME)
    assert _version_of(library) == 0


def test_failed_staging_copy_leaves_no_partial_file(
    library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy that fails partway removes the partial staging file and moves nothing."""
    _add_side_files(library)

    def copy_half_then_fail(source: str | Path, target: str | Path) -> None:
        """Write part of the file, then fail as a full disk does."""
        _ = source
        Path(target).write_bytes(b"partial")
        raise OSError("No space left on device")

    monkeypatch.setattr(backup_module.shutil, "copyfile", copy_half_then_fail)

    with pytest.raises(OSError, match="No space left"):
        restore_pre_migration_backup(library, now=RESTORE_TIME)

    _assert_library_untouched(library)


def test_stale_staging_file_from_a_crash_is_replaced(library: Path) -> None:
    """A staging file left by an earlier crash never reaches the library."""
    library.with_name(library.name + STAGED_SUFFIX).write_bytes(b"stale")

    restore_pre_migration_backup(library, now=RESTORE_TIME)

    assert _version_of(library) == 0
    with sqlite3.connect(library) as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_groups").fetchone()[0] == 1
