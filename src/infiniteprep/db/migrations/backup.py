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

from infiniteprep.db.migrations import LATEST_VERSION, ROW_COUNT_QUERIES

logger = logging.getLogger(__name__)

BACKUP_SUFFIX: Final[str] = ".pre-migration-backup"
SIDECAR_SUFFIX: Final[str] = ".pre-migration-backup.json"
TEMP_SUFFIX: Final[str] = ".tmp"
RESTORE_TEMP_SUFFIX: Final[str] = ".restore.tmp"
NEWER_COPY_INFIX: Final[str] = ".newer-"
NEWER_COPY_TIME_FORMAT: Final[str] = "%Y%m%dT%H%M%SZ"
SIDE_FILE_SUFFIXES: Final[tuple[str, ...]] = ("-wal", "-shm")
SOURCE_VERSION_KEY: Final[str] = "source_version"
ROW_COUNTS_KEY: Final[str] = "row_counts"
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


def _write_sidecar_temp(db_path: Path, source_version: int, counts: dict[str, int]) -> Path:
    """Write the backup record to a temporary file and return its path."""
    sidecar = sidecar_path_for(db_path)
    temp = sidecar.with_name(sidecar.name + TEMP_SUFFIX)
    record = {SOURCE_VERSION_KEY: source_version, ROW_COUNTS_KEY: counts}
    temp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    return temp


def create_verified_backup(
    source: sqlite3.Connection, db_path: Path, *, source_version: int
) -> Path:
    """Copy the library beside itself, verify the copy, and return the backup path.

    The previous backup stays in place until the new copy passes verification. A crash between
    the two final renames leaves a backup and record that disagree. Restore refuses that pair.
    """
    backup = backup_path_for(db_path)
    temp = backup.with_name(backup.name + TEMP_SUFFIX)
    temp.unlink(missing_ok=True)
    counts = table_row_counts(source)
    try:
        copy_database(source, temp)
        verify_backup_file(temp, expected_version=source_version, expected_counts=counts)
        sidecar_temp = _write_sidecar_temp(db_path, source_version, counts)
    except (sqlite3.Error, OSError):
        temp.unlink(missing_ok=True)
        raise
    os.replace(temp, backup)
    os.replace(sidecar_temp, sidecar_path_for(db_path))
    return backup


def _is_count_map(value: object) -> bool:
    """Return whether value maps table names to non-negative integer counts."""
    if not isinstance(value, dict):
        return False
    return all(
        isinstance(name, str) and type(count) is int and count >= 0 for name, count in value.items()
    )


def read_backup_record(db_path: Path) -> tuple[int, dict[str, int] | None] | None:
    """Return the recorded source version and row counts, or None for an unusable record.

    The counts are None when the record holds none. Restore refuses such a record.
    """
    sidecar = sidecar_path_for(db_path)
    if not sidecar.is_file():
        return None
    try:
        record = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("The backup record is unreadable. The app offers no Restore.")
        return None
    if not isinstance(record, dict):
        logger.warning("The backup record has the wrong shape. The app offers no Restore.")
        return None
    version = record.get(SOURCE_VERSION_KEY)
    if type(version) is not int or version < 0:
        logger.warning("The backup record holds no valid version. The app offers no Restore.")
        return None
    counts = record.get(ROW_COUNTS_KEY)
    return version, counts if _is_count_map(counts) else None


def read_backup_version(db_path: Path) -> int | None:
    """Return the source version the sidecar records, or None when no usable record exists."""
    record = read_backup_record(db_path)
    return None if record is None else record[0]


def usable_backup_path(db_path: Path, known_version: int) -> Path | None:
    """Return the backup path when restore would accept the backup, else None.

    The check matches the restore check. The app therefore never offers a failing Restore.
    """
    try:
        verified_backup_record(db_path, known_version=known_version)
    except (OSError, sqlite3.Error) as error:
        logger.info("The app offers no Restore. %s", type(error).__name__)
        return None
    return backup_path_for(db_path)


def _library_moves(db_path: Path, kept: Path) -> list[tuple[Path, Path]]:
    """Return the renames that move the library first, then each side file that exists."""
    moves = [(db_path, kept)]
    for suffix in SIDE_FILE_SUFFIXES:
        side = _side_file(db_path, suffix)
        if side.exists():
            moves.append((side, _side_file(kept, suffix)))
    return moves


def _check_kept_names_free(kept: Path) -> None:
    """Raise FileExistsError when a kept copy from an earlier restore holds the name."""
    targets = [kept, *(_side_file(kept, suffix) for suffix in SIDE_FILE_SUFFIXES)]
    taken = [target for target in targets if target.exists()]
    if taken:
        raise FileExistsError(
            f"{taken[0]} already exists. Wait one second and choose Restore again."
        )


def _swap_in_staged_copy(db_path: Path, kept: Path, staged: Path) -> Path | None:
    """Move the library and side files to the kept name, then install the staged copy.

    A rename that fails moves every earlier file back. The library and its side files then
    keep the original names together. Returns None when no library file existed.
    """
    moves = _library_moves(db_path, kept) if db_path.exists() else []
    done: list[tuple[Path, Path]] = []
    try:
        for source, target in moves:
            os.replace(source, target)
            done.append((source, target))
        os.replace(staged, db_path)
    except OSError:
        for source, target in reversed(done):
            os.replace(target, source)
        raise
    return kept if moves else None


def verified_backup_record(db_path: Path, *, known_version: int) -> tuple[int, dict[str, int]]:
    """Return the backup's recorded version and row counts once every check passes.

    The backup must exist, hold a complete record, match that record, and sit at a version
    no newer than known_version.
    """
    backup = backup_path_for(db_path)
    if not backup.is_file():
        raise FileNotFoundError(f"No library backup exists at {backup}.")
    record = read_backup_record(db_path)
    counts = None if record is None else record[1]
    if record is None or counts is None:
        raise BackupVerificationError(
            f"The library backup at {backup} has no usable version and row-count record."
        )
    if record[0] > known_version:
        raise BackupVerificationError(
            f"The library backup at {backup} holds version {record[0]}. This app opens "
            f"versions up to {known_version}."
        )
    verify_backup_file(backup, expected_version=record[0], expected_counts=counts)
    return record[0], counts


def restore_pre_migration_backup(
    db_path: Path, *, now: datetime | None = None, known_version: int = LATEST_VERSION
) -> Path | None:
    """Put the verified backup in place of the library and keep the library under a new name.

    Close every connection to the library first. The backup stays beside the library until
    the next clean open removes it. A failed restore leaves the library under its own name.
    A backup newer than known_version stays unused. Returns the kept copy of the library,
    or None when no library file existed.
    """
    version, counts = verified_backup_record(db_path, known_version=known_version)
    kept = newer_copy_path_for(db_path, now or _utc_now())
    _check_kept_names_free(kept)
    staged = db_path.with_name(db_path.name + RESTORE_TEMP_SUFFIX)
    staged.unlink(missing_ok=True)
    try:
        shutil.copyfile(backup_path_for(db_path), staged)
        # The swap installs the staged file. A backup changed after its check fails here.
        verify_backup_file(staged, expected_version=version, expected_counts=counts)
        return _swap_in_staged_copy(db_path, kept, staged)
    except (OSError, sqlite3.Error):
        staged.unlink(missing_ok=True)
        raise


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)
