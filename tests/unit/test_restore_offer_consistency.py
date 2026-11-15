"""Test that the app offers Restore only for a backup that restore accepts (D349)."""

from __future__ import annotations

import json
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
    sidecar_path_for,
)
from infiniteprep.db.migrations.runner import NewerLibraryError

LIBRARY_NAME = "infiniteprep.sqlite3"
RESTORE_TIME = datetime(2026, 11, 15, 11, 0, 0, tzinfo=UTC)
# A caller at version 0 knows only libraries from before numbered migrations.
PRE_MIGRATION_VERSION = 0


def _version_of(db_path: Path) -> int:
    """Read user_version through a separate connection."""
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _write_newer_library(db_path: Path) -> None:
    """Leave an empty library one version past this app."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")


def _write_backup(db_path: Path, version: int, record: dict[str, object]) -> None:
    """Write an empty backup at version and its JSON record."""
    with sqlite3.connect(backup_path_for(db_path)) as conn:
        conn.execute(f"PRAGMA user_version = {version}")
    sidecar_path_for(db_path).write_text(json.dumps(record), encoding="utf-8")


def _offered_backup(db_path: Path) -> Path | None:
    """Return the backup the refusal offers."""
    with pytest.raises(NewerLibraryError) as caught:
        DatabaseManager(db_path)
    return caught.value.backup_path


def test_record_without_row_counts_is_not_offered(tmp_path: Path) -> None:
    """Restore needs the row counts. A record that lacks the counts gets no offer."""
    db_path = tmp_path / LIBRARY_NAME
    _write_newer_library(db_path)
    _write_backup(db_path, LATEST_VERSION, {"source_version": LATEST_VERSION})

    assert _offered_backup(db_path) is None


def test_unreadable_backup_file_is_not_offered(tmp_path: Path) -> None:
    """A complete record beside a damaged backup file gets no offer."""
    db_path = tmp_path / LIBRARY_NAME
    _write_newer_library(db_path)
    backup_path_for(db_path).write_bytes(b"not a database")
    sidecar_path_for(db_path).write_text(
        json.dumps({"source_version": LATEST_VERSION, "row_counts": {}}), encoding="utf-8"
    )

    assert _offered_backup(db_path) is None


def test_complete_backup_is_offered(tmp_path: Path) -> None:
    """A verified backup at a version this app opens gets the offer."""
    db_path = tmp_path / LIBRARY_NAME
    _write_newer_library(db_path)
    _write_backup(db_path, LATEST_VERSION, {"source_version": LATEST_VERSION, "row_counts": {}})

    assert _offered_backup(db_path) == backup_path_for(db_path)


def test_restore_refuses_a_backup_newer_than_the_app(tmp_path: Path) -> None:
    """The rollback API never installs a backup this app cannot open."""
    db_path = tmp_path / LIBRARY_NAME
    newer = LATEST_VERSION + 1
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version = {newer + 1}")
    _write_backup(db_path, newer, {"source_version": newer, "row_counts": {}})

    with pytest.raises(BackupVerificationError):
        restore_pre_migration_backup(db_path, now=RESTORE_TIME)

    assert _version_of(db_path) == newer + 1
    assert list(tmp_path.glob(f"{LIBRARY_NAME}.newer-*")) == []


def test_restore_honors_a_lower_known_version(tmp_path: Path) -> None:
    """A caller that knows fewer versions refuses a backup past its own."""
    db_path = tmp_path / LIBRARY_NAME
    _write_newer_library(db_path)
    _write_backup(db_path, LATEST_VERSION, {"source_version": LATEST_VERSION, "row_counts": {}})

    with pytest.raises(BackupVerificationError):
        restore_pre_migration_backup(db_path, now=RESTORE_TIME, known_version=PRE_MIGRATION_VERSION)

    assert _version_of(db_path) == LATEST_VERSION + 1
