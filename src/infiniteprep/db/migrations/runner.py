"""Apply pending schema migrations in one transaction after one verified backup (D349)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from infiniteprep.db.migrations import MIGRATIONS, Migration
from infiniteprep.db.migrations.backup import (
    create_verified_backup,
    read_user_version,
    remove_backup,
    usable_backup_path,
)


class NewerLibraryError(sqlite3.DatabaseError):
    """The library comes from a newer app. Nothing in it changed."""

    def __init__(self, library_version: int, known_version: int, backup_path: Path | None) -> None:
        """Record both versions and the backup this app can open, if any."""
        super().__init__(
            f"The library is at schema version {library_version}. "
            f"This app opens versions up to {known_version}."
        )
        self.library_version = library_version
        self.known_version = known_version
        self.backup_path = backup_path


class MigrationFailedError(sqlite3.DatabaseError):
    """A migration step failed. The run rolled back, and the backup stays in place."""

    def __init__(self, failed_version: int, backup_path: Path | None) -> None:
        """Record the step that failed and the backup taken before the run."""
        super().__init__(
            f"Library schema migration {failed_version} failed. Rollback undid every step."
        )
        self.failed_version = failed_version
        self.backup_path = backup_path


def _check_order(migrations: Sequence[Migration]) -> None:
    """Raise ValueError unless the versions run 1, 2, 3, and so on with no gap."""
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(f"Migration versions must run from 1 with no gap, not {versions}.")


def _has_tables(conn: sqlite3.Connection) -> bool:
    """Return whether the database holds any table. A new library holds none."""
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1").fetchone()
    return row is not None


def _apply_in_transaction(
    conn: sqlite3.Connection, pending: Sequence[Migration], backup_path: Path | None
) -> None:
    """Run every pending step and the version stamp. Any error rolls back the whole run."""
    target_version = pending[-1].version
    failed_version = pending[0].version
    conn.execute("BEGIN IMMEDIATE")
    try:
        for migration in pending:
            failed_version = migration.version
            migration.apply(conn)
        # PRAGMA accepts no bound parameters. The value is an int from the registry.
        conn.execute(f"PRAGMA user_version = {int(target_version)}")
        conn.commit()
    except Exception as error:
        # A step can raise any error type. Every one must undo the whole run.
        conn.rollback()
        raise MigrationFailedError(failed_version, backup_path) from error


def migrate_database(
    conn: sqlite3.Connection,
    db_path: Path | None,
    *,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> int:
    """Bring the library to the latest version and return that version.

    db_path is None for an in-memory database. That database gets no backup. A start with nothing
    pending deletes the backup the previous migrating start left.
    """
    _check_order(migrations)
    known_version = migrations[-1].version
    current_version = read_user_version(conn)
    if current_version > known_version:
        backup = None if db_path is None else usable_backup_path(db_path, known_version)
        raise NewerLibraryError(current_version, known_version, backup)
    pending = [migration for migration in migrations if migration.version > current_version]
    if not pending:
        if db_path is not None:
            remove_backup(db_path)
        return current_version
    backup_path = None
    if db_path is not None and _has_tables(conn):
        backup_path = create_verified_backup(conn, db_path, source_version=current_version)
    _apply_in_transaction(conn, pending, backup_path)
    return known_version
