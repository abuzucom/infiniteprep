"""Test that the app offers Restore only for a backup that restore accepts (D349)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import LATEST_VERSION
from infiniteprep.db.migrations.backup import (
    BackupVerificationError,
    backup_path_for,
    restore_pre_migration_backup,
    write_backup_record,
)
from infiniteprep.db.migrations.runner import NewerLibraryError
from tests.fixtures.sqlite_version import read_user_version

LIBRARY_NAME = "infiniteprep.sqlite3"
RESTORE_TIME = datetime(2026, 11, 15, 11, 0, 0, tzinfo=UTC)
# A caller at version 0 knows only libraries from before numbered migrations.
PRE_MIGRATION_VERSION = 0


def _write_newer_library(db_path: Path) -> None:
    """Leave an empty library one version past this app."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")


def _write_backup(db_path: Path, version: int, counts: dict[str, int] | None) -> None:
    """Write an empty backup at version. None for counts leaves the backup without a record."""
    with sqlite3.connect(backup_path_for(db_path)) as conn:
        conn.execute(f"PRAGMA user_version = {version}")
    if counts is not None:
        write_backup_record(backup_path_for(db_path), source_version=version, row_counts=counts)


def _offered_backup(db_path: Path) -> Path | None:
    """Return the backup the refusal offers."""
    with pytest.raises(NewerLibraryError) as caught:
        DatabaseManager(db_path)
    return caught.value.backup_path


def test_record_without_counts_not_offered(tmp_path: Path) -> None:
    """Restore needs the row counts. A record that lacks the counts gets no offer."""
    db_path = tmp_path / LIBRARY_NAME
    _write_newer_library(db_path)
    _write_backup(db_path, LATEST_VERSION, None)

    assert _offered_backup(db_path) is None


def test_unreadable_backup_is_not_offered(tmp_path: Path) -> None:
    """A complete record beside a damaged backup file gets no offer."""
    db_path = tmp_path / LIBRARY_NAME
    _write_newer_library(db_path)
    backup_path_for(db_path).write_bytes(b"not a database")

    assert _offered_backup(db_path) is None


def test_complete_backup_is_offered(tmp_path: Path) -> None:
    """A verified backup at a version this app opens gets the offer."""
    db_path = tmp_path / LIBRARY_NAME
    _write_newer_library(db_path)
    _write_backup(db_path, LATEST_VERSION, {})

    assert _offered_backup(db_path) == backup_path_for(db_path)


def test_restore_refuses_newer_backup(tmp_path: Path) -> None:
    """The rollback API never installs a backup this app cannot open."""
    db_path = tmp_path / LIBRARY_NAME
    newer = LATEST_VERSION + 1
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version = {newer + 1}")
    _write_backup(db_path, newer, {})

    with pytest.raises(BackupVerificationError):
        restore_pre_migration_backup(db_path, now=RESTORE_TIME)

    assert read_user_version(db_path) == newer + 1
    assert list(tmp_path.glob(f"{LIBRARY_NAME}.newer-*")) == []


def test_restore_honors_lower_known_version(tmp_path: Path) -> None:
    """A caller that knows fewer versions refuses a backup past its own."""
    db_path = tmp_path / LIBRARY_NAME
    _write_newer_library(db_path)
    _write_backup(db_path, LATEST_VERSION, {})

    with pytest.raises(BackupVerificationError):
        restore_pre_migration_backup(db_path, now=RESTORE_TIME, known_version=PRE_MIGRATION_VERSION)

    assert read_user_version(db_path) == LATEST_VERSION + 1
