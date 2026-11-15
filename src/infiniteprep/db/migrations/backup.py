"""The verified pre-migration backup beside the library, and its restore (D349).

UPD-3 calls restore_pre_migration_backup() to undo a migration on an update rollback. The
newer-library prompt at startup calls it too.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from infiniteprep.db.migrations import ROW_COUNT_QUERIES

logger = logging.getLogger(__name__)

BACKUP_SUFFIX: Final[str] = ".pre-migration-backup"
SIDECAR_SUFFIX: Final[str] = ".pre-migration-backup.json"
TEMP_SUFFIX: Final[str] = ".tmp"
RESTORE_TEMP_SUFFIX: Final[str] = ".restore.tmp"
NEWER_COPY_INFIX: Final[str] = ".newer-"
NEWER_COPY_TIME_FORMAT: Final[str] = "%Y%m%dT%H%M%SZ"
SIDE_FILE_SUFFIXES: Final[tuple[str, ...]] = ("-wal", "-shm")
SOURCE_VERSION_KEY: Final[str] = "source_version"
INTEGRITY_OK: Final[str] = "ok"


class BackupVerificationError(sqlite3.DatabaseError):
    """The backup copy failed its checks. The caller migrates and restores nothing."""


def backup_path_for(db_path: Path) -> Path:
    """Return the backup file path beside the library."""
    return db_path.with_name(db_path.name + BACKUP_SUFFIX)


def sidecar_path_for(db_path: Path) -> Path:
    """Return the sidecar path that records the backup's source version."""
    return db_path.with_name(db_path.name + SIDECAR_SUFFIX)


def newer_copy_path_for(db_path: Path, when: datetime) -> Path:
    """Return the name a restore gives the newer library it keeps."""
    stamp = when.astimezone(UTC).strftime(NEWER_COPY_TIME_FORMAT)
    return db_path.with_name(f"{db_path.name}{NEWER_COPY_INFIX}{stamp}")


def _side_file(path: Path, suffix: str) -> Path:
    """Return the WAL or SHM file path of a database file."""
    return path.with_name(path.name + suffix)


def table_row_counts(
    conn: sqlite3.Connection, queries: Mapping[str, str] = ROW_COUNT_QUERIES
) -> dict[str, int]:
    """Return the row count of each registered table that the database holds."""
    present = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    return {
        table: int(conn.execute(query).fetchone()[0])
        for table, query in queries.items()
        if table in present
    }


def read_user_version(conn: sqlite3.Connection) -> int:
    """Return the schema version stored in the database header."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def copy_database(source: sqlite3.Connection, target: Path) -> None:
    """Copy a live database with SQLite's online backup API.

    The copy switches to a rollback journal. A read-only open then needs no -wal or -shm file.
    """
    target_conn = sqlite3.connect(target)
    try:
        source.backup(target_conn)
        target_conn.execute("PRAGMA journal_mode = DELETE")
    finally:
        target_conn.close()


def _open_read_only(path: Path) -> sqlite3.Connection:
    """Open a database file read-only."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def verify_backup_file(
    path: Path, *, expected_version: int, expected_counts: dict[str, int] | None
) -> None:
    """Raise BackupVerificationError unless the copy passes every check against the source."""
    try:
        conn = _open_read_only(path)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            version = read_user_version(conn)
            counts = table_row_counts(conn)
        finally:
            conn.close()
    except sqlite3.DatabaseError as error:
        raise BackupVerificationError(
            f"SQLite cannot read the library backup at {path} ({error}). Free disk space and "
            "start InfinitePrep again."
        ) from error
    if integrity != INTEGRITY_OK:
        raise BackupVerificationError(f"The library backup at {path} failed its integrity check.")
    if version != expected_version:
        raise BackupVerificationError(
            f"The library backup at {path} holds version {version}, not {expected_version}."
        )
    if expected_counts is not None and counts != expected_counts:
        raise BackupVerificationError(
            f"The library backup at {path} does not match the library row for row."
        )


def _unlink_quietly(paths: list[Path]) -> None:
    """Delete each path that exists."""
    for path in paths:
        path.unlink(missing_ok=True)


def remove_backup(db_path: Path) -> None:
    """Delete the backup and its sidecar once a later start opens cleanly."""
    _unlink_quietly([backup_path_for(db_path), sidecar_path_for(db_path)])


def _write_sidecar(db_path: Path, source_version: int) -> None:
    """Record the backup's source version beside it, replacing any earlier record."""
    sidecar = sidecar_path_for(db_path)
    temp = sidecar.with_name(sidecar.name + TEMP_SUFFIX)
    temp.write_text(json.dumps({SOURCE_VERSION_KEY: source_version}), encoding="utf-8")
    os.replace(temp, sidecar)


def create_verified_backup(
    source: sqlite3.Connection, db_path: Path, *, source_version: int
) -> Path:
    """Copy the library beside itself, verify the copy, and return the backup path.

    The previous backup stays in place until the new copy passes verification.
    """
    backup = backup_path_for(db_path)
    temp = backup.with_name(backup.name + TEMP_SUFFIX)
    temp.unlink(missing_ok=True)
    try:
        copy_database(source, temp)
        verify_backup_file(
            temp, expected_version=source_version, expected_counts=table_row_counts(source)
        )
    except (sqlite3.Error, OSError):
        temp.unlink(missing_ok=True)
        raise
    sidecar_path_for(db_path).unlink(missing_ok=True)
    os.replace(temp, backup)
    _write_sidecar(db_path, source_version)
    return backup


def read_backup_version(db_path: Path) -> int | None:
    """Return the source version the sidecar records, or None when no usable record exists."""
    sidecar = sidecar_path_for(db_path)
    if not sidecar.is_file():
        return None
    try:
        record = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("The library backup record is unreadable. The app offers no Restore.")
        return None
    version = record.get(SOURCE_VERSION_KEY) if isinstance(record, dict) else None
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        logger.warning(
            "The library backup record holds no valid version. The app offers no Restore."
        )
        return None
    return version


def usable_backup_path(db_path: Path, known_version: int) -> Path | None:
    """Return the backup path when a backup exists that this app can open, else None."""
    version = read_backup_version(db_path)
    backup = backup_path_for(db_path)
    if version is None or version > known_version or not backup.is_file():
        return None
    return backup


def _move_newer_library(db_path: Path, kept: Path) -> Path | None:
    """Rename the library and its side files to the kept name. Return None with no library."""
    targets = [kept, *(_side_file(kept, suffix) for suffix in SIDE_FILE_SUFFIXES)]
    taken = [target for target in targets if target.exists()]
    if taken:
        raise FileExistsError(
            f"{taken[0]} already exists. Wait one second and choose Restore again."
        )
    if not db_path.exists():
        return None
    for suffix in SIDE_FILE_SUFFIXES:
        side = _side_file(db_path, suffix)
        if side.exists():
            os.replace(side, _side_file(kept, suffix))
    os.replace(db_path, kept)
    return kept


def restore_pre_migration_backup(db_path: Path, *, now: datetime | None = None) -> Path | None:
    """Put the verified backup in place of the library and keep the library under a new name.

    Close every connection to the library first. The backup stays beside the library until
    the next clean open removes it. An interrupted restore can therefore run again. Returns
    the kept copy of the library, or None when no library file existed.
    """
    backup = backup_path_for(db_path)
    if not backup.is_file():
        raise FileNotFoundError(f"No library backup exists at {backup}.")
    expected_version = read_backup_version(db_path)
    if expected_version is None:
        raise BackupVerificationError(
            f"The library backup at {backup} has no readable version record."
        )
    verify_backup_file(backup, expected_version=expected_version, expected_counts=None)
    staged = db_path.with_name(db_path.name + RESTORE_TEMP_SUFFIX)
    shutil.copyfile(backup, staged)
    try:
        kept = _move_newer_library(db_path, newer_copy_path_for(db_path, now or _utc_now()))
    except OSError:
        staged.unlink(missing_ok=True)
        raise
    os.replace(staged, db_path)
    return kept


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)
