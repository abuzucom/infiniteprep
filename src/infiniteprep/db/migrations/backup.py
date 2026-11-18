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
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from infiniteprep.db.migrations import LATEST_VERSION, ROW_COUNT_QUERIES
from infiniteprep.private_files import (
    create_new_private_file,
    private_staging_dir,
    resolve_library_path,
)

logger = logging.getLogger(__name__)

BACKUP_SUFFIX: Final[str] = ".pre-migration-backup"
SIDECAR_SUFFIX: Final[str] = ".pre-migration-backup.json"
TEMPORARY_SUFFIX: Final[str] = ".tmp"
RESTORE_TEMPORARY_SUFFIX: Final[str] = ".restore.tmp"
NEWER_COPY_INFIX: Final[str] = ".newer-"
NEWER_COPY_TIME_FORMAT: Final[str] = "%Y%m%dT%H%M%SZ"
SIDE_FILE_SUFFIXES: Final[tuple[str, ...]] = ("-wal", "-shm")
INTEGRITY_OK: Final[str] = "ok"
# The record lives inside the backup file. One rename then publishes the copy and its record.
RECORD_TABLE: Final[str] = "infiniteprep_backup_record"
CREATE_RECORD_SQL: Final[str] = (
    "CREATE TABLE infiniteprep_backup_record "
    "(source_version INTEGER NOT NULL, row_counts TEXT NOT NULL)"
)
INSERT_RECORD_SQL: Final[str] = (
    "INSERT INTO infiniteprep_backup_record (source_version, row_counts) VALUES (?, ?)"
)
SELECT_RECORD_SQL: Final[str] = "SELECT source_version, row_counts FROM infiniteprep_backup_record"
DROP_RECORD_SQL: Final[str] = "DROP TABLE IF EXISTS infiniteprep_backup_record"
HAS_TABLE_SQL: Final[str] = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"


class BackupVerificationError(sqlite3.DatabaseError):
    """The backup copy failed its checks. The caller migrates and restores nothing."""


def backup_path_for(db_path: Path) -> Path:
    """Return the backup file path beside the library."""
    return db_path.with_name(db_path.name + BACKUP_SUFFIX)


def sidecar_path_for(db_path: Path) -> Path:
    """Return the separate record path the app never writes. Cleanup removes a stray file."""
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
    # The copy holds the whole library. It starts as a new owner-only file. A name that
    # already exists, a planted link included, fails the copy.
    create_new_private_file(target)
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
    """Delete the backup and any stray record file once a later start opens cleanly."""
    _unlink_quietly([backup_path_for(db_path), sidecar_path_for(db_path)])


def write_backup_record(path: Path, *, source_version: int, row_counts: Mapping[str, int]) -> None:
    """Store the source version and row counts as a one-row table inside the backup file."""
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(DROP_RECORD_SQL)
        conn.execute(CREATE_RECORD_SQL)
        conn.execute(INSERT_RECORD_SQL, (source_version, json.dumps(dict(row_counts))))


def _record_in_copy(path: Path, source_version: int, counts: Mapping[str, int]) -> None:
    """Write the record into a fresh copy. An unwritable copy fails verification."""
    try:
        write_backup_record(path, source_version=source_version, row_counts=counts)
    except sqlite3.DatabaseError as error:
        raise BackupVerificationError(
            f"SQLite cannot write the record into the library backup at {path} ({error}). "
            "Free disk space and start InfinitePrep again."
        ) from error


def _drop_backup_record(path: Path) -> None:
    """Remove the record table from a staged copy before the swap installs it."""
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(DROP_RECORD_SQL)


def create_verified_backup(
    source: sqlite3.Connection, db_path: Path, *, source_version: int
) -> Path:
    """Copy the library beside itself, verify the copy, and return the backup path.

    The copy carries its own record. One rename publishes both. The previous backup stays in
    place until the new copy passes verification. source may hold the write lock. The copy
    then reads through a second connection, since SQLite refuses a backup from a connection
    that holds the write lock. The lock still keeps other writers out during the copy.
    """
    # Every later step uses the real folder. A link above the library cannot move them.
    db_path = resolve_library_path(db_path)
    backup = backup_path_for(db_path)
    counts = table_row_counts(source)
    # The copy, its record write, and its check reopen the file by name. The owner-only
    # staging folder keeps another account from swapping that name between the steps. Each
    # run gets its own folder. Two instances never share a temporary file.
    with private_staging_dir(db_path.parent) as staging:
        temporary_copy = staging / f"{backup.name}{TEMPORARY_SUFFIX}"
        with closing(sqlite3.connect(db_path)) as reader:
            copy_database(reader, temporary_copy)
        _record_in_copy(temporary_copy, source_version, counts)
        verify_backup_file(temporary_copy, expected_version=source_version, expected_counts=counts)
        os.replace(temporary_copy, backup)
    sidecar_path_for(db_path).unlink(missing_ok=True)
    return backup


def _is_count_map(value: object) -> bool:
    """Return whether value maps table names to non-negative integer counts."""
    if not isinstance(value, dict):
        return False
    return all(
        isinstance(name, str) and type(count) is int and count >= 0 for name, count in value.items()
    )


def _read_record_rows(backup: Path) -> list[tuple[object, object]] | None:
    """Return the record table rows of the backup, or None when the table is absent."""
    with closing(_open_read_only(backup)) as conn:
        if conn.execute(HAS_TABLE_SQL, (RECORD_TABLE,)).fetchone() is None:
            return None
        return [tuple(row) for row in conn.execute(SELECT_RECORD_SQL).fetchall()]


def read_backup_record(db_path: Path) -> tuple[int, dict[str, int] | None] | None:
    """Return the source version and row counts the backup records, or None if unusable.

    The counts are None when the record holds no valid count map. Restore refuses that record.
    """
    backup = backup_path_for(db_path)
    if not backup.is_file():
        return None
    try:
        rows = _read_record_rows(backup)
    except sqlite3.DatabaseError:
        logger.warning("The backup record is unreadable. The app offers no Restore.")
        return None
    if rows is None or len(rows) != 1:
        logger.warning("The backup holds no single record row. The app offers no Restore.")
        return None
    version, counts_text = rows[0]
    if type(version) is not int or version < 0 or not isinstance(counts_text, str):
        logger.warning("The backup record holds invalid values. The app offers no Restore.")
        return None
    try:
        counts = json.loads(counts_text)
    except ValueError:
        return version, None
    return version, counts if _is_count_map(counts) else None


def read_backup_version(db_path: Path) -> int | None:
    """Return the source version the backup's record table holds, or None without a record."""
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
    # Every later step uses the real folder. A link above the library cannot move them.
    db_path = resolve_library_path(db_path)
    version, counts = verified_backup_record(db_path, known_version=known_version)
    kept = newer_copy_path_for(db_path, now or _utc_now())
    _check_kept_names_free(kept)
    # Earlier versions staged the copy beside the library. A crash there left this name.
    db_path.with_name(db_path.name + RESTORE_TEMPORARY_SUFFIX).unlink(missing_ok=True)
    # The copy, the record drop, and the check reopen the staged file by name. The owner-only
    # staging folder keeps another account from swapping that name between the steps.
    with private_staging_dir(db_path.parent) as staging:
        staged = staging / (db_path.name + RESTORE_TEMPORARY_SUFFIX)
        create_new_private_file(staged)
        shutil.copyfile(backup_path_for(db_path), staged)
        _drop_backup_record(staged)
        # The swap installs the staged file. A backup changed after its check fails here.
        verify_backup_file(staged, expected_version=version, expected_counts=counts)
        return _swap_in_staged_copy(db_path, kept, staged)


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)
