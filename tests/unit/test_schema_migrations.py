"""Test numbered schema migrations on user_version with a verified backup (D349)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import LATEST_VERSION, MIGRATIONS, Migration
from infiniteprep.db.migrations import backup as backup_module
from infiniteprep.db.migrations.backup import (
    BackupVerificationError,
    backup_path_for,
    create_verified_backup,
    read_backup_version,
    sidecar_path_for,
)
from infiniteprep.db.migrations.runner import (
    MigrationFailedError,
    NewerLibraryError,
    migrate_database,
)

LIBRARY_NAME = "infiniteprep.sqlite3"
SCHEMA_TABLES = {
    "source_groups",
    "file_assets",
    "candidate_matches",
    "field_proposals",
    "analysis_results",
    "approved_fields",
    "change_records",
    "jobs",
    "provider_cache",
}
SOURCE_GROUP_ROW = ("group-1", "Crate", "folder", "/music/crate")


class InjectedMigrationError(RuntimeError):
    """Raised by a test migration to force a mid-run failure."""


def _user_version(db_path: Path) -> int:
    """Read user_version through a separate connection."""
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _table_names(db_path: Path) -> set[str]:
    """Return the names of the application tables in the library at db_path."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {row[0] for row in rows}


def _column_names(db_path: Path, table: str) -> set[str]:
    """Return the column names of a table that the test itself created."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM pragma_table_info(?)", (table,)).fetchall()
    return {row[0] for row in rows}


def _create_version_zero_library(db_path: Path) -> None:
    """Build a library the way every release before D349 left it: schema, data, version 0."""
    manager_conn = sqlite3.connect(db_path)
    try:
        for migration in MIGRATIONS:
            migration.apply(manager_conn)
        manager_conn.execute(
            "INSERT INTO source_groups (id, user_label, grouping_basis, original_folder) "
            "VALUES (?, ?, ?, ?)",
            SOURCE_GROUP_ROW,
        )
        manager_conn.execute("PRAGMA user_version = 0")
        manager_conn.commit()
    finally:
        manager_conn.close()


def _add_notes_table(conn: sqlite3.Connection) -> None:
    """Test migration 2: add a table."""
    conn.execute("CREATE TABLE crate_notes (id TEXT PRIMARY KEY, body TEXT NOT NULL)")


def _add_notes_column(conn: sqlite3.Connection) -> None:
    """Test migration 3: add a column to the table from migration 2."""
    conn.execute("ALTER TABLE crate_notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")


def _fail_after_ddl(conn: sqlite3.Connection) -> None:
    """Test migration that changes the schema, then fails."""
    conn.execute("CREATE TABLE half_done (id TEXT PRIMARY KEY)")
    raise InjectedMigrationError("forced failure")


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """Return a library path inside a fresh data folder."""
    return tmp_path / "Data" / LIBRARY_NAME


@pytest.fixture
def opened(library: Path) -> Iterator[sqlite3.Connection]:
    """Open a raw connection the way DatabaseManager does, and close it afterward."""
    library.parent.mkdir(parents=True, exist_ok=True)
    _create_version_zero_library(library)
    conn = sqlite3.connect(library)
    conn.execute("PRAGMA journal_mode = WAL")
    yield conn
    conn.close()


def test_migration_one_is_the_current_schema() -> None:
    """The registry starts at version 1 and stays in strict ascending order."""
    versions = [migration.version for migration in MIGRATIONS]

    assert versions[0] == 1
    assert versions == sorted(set(versions))
    assert versions[-1] == LATEST_VERSION


def test_fresh_library_reaches_latest_without_backup(library: Path) -> None:
    """A new library gets every table and the latest version, and needs no backup."""
    with DatabaseManager(library):
        pass

    assert _user_version(library) == LATEST_VERSION
    assert _table_names(library) >= SCHEMA_TABLES
    assert not backup_path_for(library).exists()
    assert not sidecar_path_for(library).exists()


def test_in_memory_library_reaches_latest() -> None:
    """An in-memory database migrates with no file and no backup."""
    with DatabaseManager(":memory:") as manager:
        conn = manager.connect()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert version == LATEST_VERSION
    assert tables >= SCHEMA_TABLES


def test_existing_version_zero_library_keeps_data_and_gets_backup(library: Path) -> None:
    """A pre-D349 library reaches version 1 unchanged and leaves a verified version-0 backup."""
    library.parent.mkdir(parents=True)
    _create_version_zero_library(library)

    with DatabaseManager(library) as manager:
        row = manager.connect().execute("SELECT id, user_label FROM source_groups").fetchone()

    assert tuple(row) == SOURCE_GROUP_ROW[:2]
    assert _user_version(library) == LATEST_VERSION
    assert backup_path_for(library).is_file()
    assert _user_version(backup_path_for(library)) == 0
    assert read_backup_version(library) == 0
    with sqlite3.connect(backup_path_for(library)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_groups").fetchone()[0] == 1


def test_next_clean_open_removes_the_backup(library: Path) -> None:
    """The start after a migrating start finds nothing pending and deletes the backup."""
    library.parent.mkdir(parents=True)
    _create_version_zero_library(library)
    with DatabaseManager(library):
        pass
    assert backup_path_for(library).is_file()

    with DatabaseManager(library):
        pass

    assert not backup_path_for(library).exists()
    assert not sidecar_path_for(library).exists()


def test_two_step_migration_runs_in_order(library: Path, opened: sqlite3.Connection) -> None:
    """Steps 2 and 3 run in order in one start, after one backup of version 0."""
    steps = (*MIGRATIONS, Migration(2, _add_notes_table), Migration(3, _add_notes_column))

    reached = migrate_database(opened, library, migrations=steps)

    assert reached == 3
    assert _user_version(library) == 3
    assert {"id", "body", "pinned"} == _column_names(library, "crate_notes")
    assert read_backup_version(library) == 0
    assert "crate_notes" not in _table_names(backup_path_for(library))


def test_failed_migration_rolls_back_and_keeps_backup(
    library: Path, opened: sqlite3.Connection
) -> None:
    """A step that fails undoes every step of the run and leaves the backup intact."""
    steps = (*MIGRATIONS, Migration(2, _add_notes_table), Migration(3, _fail_after_ddl))

    with pytest.raises(MigrationFailedError) as caught:
        migrate_database(opened, library, migrations=steps)

    assert caught.value.failed_version == 3
    assert caught.value.backup_path == backup_path_for(library)
    assert isinstance(caught.value.__cause__, InjectedMigrationError)
    assert _user_version(library) == 0
    assert not {"crate_notes", "half_done"} & _table_names(library)
    with sqlite3.connect(backup_path_for(library)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM source_groups").fetchone()[0] == 1
    assert read_backup_version(library) == 0


def test_newer_library_is_refused_untouched(library: Path) -> None:
    """A library from a newer app raises before any change and names the usable backup."""
    library.parent.mkdir(parents=True)
    newer_version = LATEST_VERSION + 1
    with sqlite3.connect(library) as conn:
        conn.execute(f"PRAGMA user_version = {newer_version}")
    with sqlite3.connect(backup_path_for(library)) as conn:
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION}")
    sidecar_path_for(library).write_text(
        json.dumps({"source_version": LATEST_VERSION, "row_counts": {}}), encoding="utf-8"
    )

    with pytest.raises(NewerLibraryError) as caught:
        DatabaseManager(library)

    assert isinstance(caught.value, sqlite3.DatabaseError)
    assert caught.value.library_version == newer_version
    assert caught.value.known_version == LATEST_VERSION
    assert caught.value.backup_path == backup_path_for(library)
    assert _user_version(library) == newer_version
    assert _table_names(library) == set()
    assert backup_path_for(library).is_file()


def test_newer_library_without_usable_backup_offers_none(library: Path) -> None:
    """A backup that is itself newer than the app is never offered."""
    library.parent.mkdir(parents=True)
    newer_version = LATEST_VERSION + 2
    with sqlite3.connect(library) as conn:
        conn.execute(f"PRAGMA user_version = {newer_version}")
    with sqlite3.connect(backup_path_for(library)) as conn:
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")
    sidecar_path_for(library).write_text(
        json.dumps({"source_version": LATEST_VERSION + 1}), encoding="utf-8"
    )

    with pytest.raises(NewerLibraryError) as caught:
        DatabaseManager(library)

    assert caught.value.backup_path is None


def test_refused_open_releases_the_library_file(library: Path) -> None:
    """A refused open closes its connection. A later rename of the file succeeds."""
    library.parent.mkdir(parents=True)
    with sqlite3.connect(library) as conn:
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")

    with pytest.raises(NewerLibraryError):
        DatabaseManager(library)

    moved = library.with_name("moved.sqlite3")
    library.rename(moved)
    assert moved.is_file()


def test_backup_verification_rejects_a_mismatched_copy(
    library: Path, opened: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verification rejects a copy whose row counts differ from the source. No backup remains."""
    real_copy = backup_module.copy_database

    def copy_then_drop_row(source: sqlite3.Connection, target: Path) -> None:
        """Copy faithfully, then damage the copy."""
        real_copy(source, target)
        with sqlite3.connect(target) as damaged:
            damaged.execute("DELETE FROM source_groups")

    monkeypatch.setattr(backup_module, "copy_database", copy_then_drop_row)

    with pytest.raises(BackupVerificationError):
        create_verified_backup(opened, library, source_version=0)

    assert not backup_path_for(library).exists()
    assert not sidecar_path_for(library).exists()


def test_failed_backup_stops_migration(
    library: Path, opened: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backup that fails verification stops every step."""

    def refuse_copy(source: sqlite3.Connection, target: Path) -> None:
        """Simulate a copy that leaves an unreadable file."""
        _ = source
        target.write_bytes(b"not a database")

    monkeypatch.setattr(backup_module, "copy_database", refuse_copy)
    steps = (*MIGRATIONS, Migration(2, _add_notes_table))

    with pytest.raises(BackupVerificationError):
        migrate_database(opened, library, migrations=steps)

    assert _user_version(library) == 0
    assert "crate_notes" not in _table_names(library)
