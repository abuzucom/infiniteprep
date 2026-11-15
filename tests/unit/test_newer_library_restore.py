"""Test the newer-library refusal and the Restore swap (D349)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt

from infiniteprep import storage_paths
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import LATEST_VERSION
from infiniteprep.db.migrations.backup import (
    BackupVerificationError,
    backup_path_for,
    newer_copy_path_for,
    restore_pre_migration_backup,
)
from infiniteprep.startup_storage import (
    DATABASE_FILE_NAME,
    QUIT_EXIT_CODE,
    STORAGE_ERROR_EXIT_CODE,
    OpenedStorage,
    open_storage,
)
from infiniteprep.storage_paths import FallbackAnswer, StorageRegistry
from infiniteprep.ui.storage_prompt import (
    RESTORE_LABEL,
    build_restore_prompt,
)

EXE_NAME = "InfinitePrep.exe"
PER_USER_DIR_FUNCTIONS = ("user_data_dir", "user_config_dir", "user_cache_dir")
RESTORE_TIME = datetime(2026, 11, 15, 9, 30, 5, tzinfo=UTC)
NEWER_ONLY_TABLE = "newer_only"
SIDE_FILE_SUFFIXES = ("-wal", "-shm")


def _version_of(db_path: Path) -> int:
    """Read user_version through a separate connection."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _make_migrated_library(db_path: Path) -> None:
    """Leave a library at the latest version with a backup from version 0 beside it."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO legacy (id) VALUES (1)")
    DatabaseManager(db_path).close()


def _upgrade_to_newer_version(db_path: Path) -> None:
    """Simulate a newer app: add a table and raise user_version past this app."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(f"CREATE TABLE {NEWER_ONLY_TABLE} (id INTEGER PRIMARY KEY)")
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """Return a migrated library path whose newer app has since run."""
    db_path = tmp_path / "Data" / DATABASE_FILE_NAME
    _make_migrated_library(db_path)
    _upgrade_to_newer_version(db_path)
    return db_path


def test_restore_puts_backup_in_place_and_keeps_newer_copy(library: Path) -> None:
    """Restore keeps the newer library under a timestamped name and installs the backup."""
    kept = restore_pre_migration_backup(library, now=RESTORE_TIME)

    assert kept == newer_copy_path_for(library, RESTORE_TIME)
    assert kept.name == f"{DATABASE_FILE_NAME}.newer-20261115T093005Z"
    assert _version_of(kept) == LATEST_VERSION + 1
    assert _version_of(library) == 0
    with closing(sqlite3.connect(library)) as conn, conn:
        assert conn.execute("SELECT id FROM legacy").fetchone()[0] == 1


def test_restore_moves_wal_and_shm_with_the_newer_copy(library: Path) -> None:
    """Side files of the newer library travel with it and never attach to the backup."""
    for suffix in SIDE_FILE_SUFFIXES:
        Path(f"{library}{suffix}").write_bytes(b"side file")

    kept = restore_pre_migration_backup(library, now=RESTORE_TIME)

    for suffix in SIDE_FILE_SUFFIXES:
        assert Path(f"{kept}{suffix}").read_bytes() == b"side file"
        assert not Path(f"{library}{suffix}").exists()


def test_restored_library_opens_and_migrates(library: Path) -> None:
    """The app opens the restored library, migrates it, and keeps the backup for rollback."""
    restore_pre_migration_backup(library, now=RESTORE_TIME)

    with DatabaseManager(library):
        pass

    assert _version_of(library) == LATEST_VERSION
    assert backup_path_for(library).is_file()


def test_restore_without_backup_changes_nothing(tmp_path: Path) -> None:
    """With no backup, restore raises and leaves the library where it was."""
    db_path = tmp_path / DATABASE_FILE_NAME
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")

    with pytest.raises(FileNotFoundError):
        restore_pre_migration_backup(db_path, now=RESTORE_TIME)

    assert _version_of(db_path) == LATEST_VERSION + 1
    assert not newer_copy_path_for(db_path, RESTORE_TIME).exists()


def test_restore_rejects_backup_that_disagrees_with_sidecar(library: Path) -> None:
    """A backup whose version differs from its sidecar is never installed."""
    with closing(sqlite3.connect(backup_path_for(library))) as conn, conn:
        conn.execute("PRAGMA user_version = 7")

    with pytest.raises(BackupVerificationError):
        restore_pre_migration_backup(library, now=RESTORE_TIME)

    assert _version_of(library) == LATEST_VERSION + 1
    assert backup_path_for(library).is_file()


def test_restore_refuses_to_overwrite_an_existing_copy(library: Path) -> None:
    """A second restore in the same second never overwrites the first kept copy."""
    newer_copy_path_for(library, RESTORE_TIME).write_bytes(b"earlier copy")

    with pytest.raises(FileExistsError):
        restore_pre_migration_backup(library, now=RESTORE_TIME)

    assert newer_copy_path_for(library, RESTORE_TIME).read_bytes() == b"earlier copy"
    assert _version_of(library) == LATEST_VERSION + 1


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Isolate the per-user folders and the registry, and record prompts and errors."""
    for name in PER_USER_DIR_FUNCTIONS:
        monkeypatch.setattr(storage_paths.platformdirs, name, lambda *_, n=name: str(tmp_path / n))
    monkeypatch.setattr(storage_paths, "STORAGE_REGISTRY", StorageRegistry())
    app_folder = tmp_path / "InfinitePrep"
    app_folder.mkdir()
    (app_folder / EXE_NAME).write_bytes(b"")
    db_path = tmp_path / "user_data_dir" / DATABASE_FILE_NAME
    _make_migrated_library(db_path)
    _upgrade_to_newer_version(db_path)
    return {"app": app_folder, "db": db_path, "errors": [], "prompts": []}


def _open(env: dict[str, Any], *, restore: bool | None) -> OpenedStorage | int:
    """Run open_storage. restore=None passes no restore confirmer at all."""

    def confirm_restore(message: str) -> bool:
        env["prompts"].append(message)
        return bool(restore)

    return open_storage(
        env["app"] / EXE_NAME,
        frozen=False,
        confirm_fallback=lambda _folder: FallbackAnswer(use_per_user=False, remember=False),
        show_error=env["errors"].append,
        confirm_restore=None if restore is None else confirm_restore,
    )


def test_open_storage_restores_on_confirmation(env: dict[str, Any]) -> None:
    """Confirming Restore swaps in the backup. The start continues."""
    opened = _open(env, restore=True)

    assert isinstance(opened, OpenedStorage)
    opened.repository.db.close()
    [message] = env["prompts"]
    assert str(backup_path_for(env["db"])) in message
    assert f"{DATABASE_FILE_NAME}.newer-" in message
    assert env["errors"] == []
    assert _version_of(env["db"]) == LATEST_VERSION
    kept = list(env["db"].parent.glob(f"{DATABASE_FILE_NAME}.newer-*"))
    assert len(kept) == 1
    assert kept[0].name in message


def test_open_storage_quits_when_restore_declined(env: dict[str, Any]) -> None:
    """Declining Restore leaves every file alone. The start ends."""
    assert _open(env, restore=False) == QUIT_EXIT_CODE

    assert _version_of(env["db"]) == LATEST_VERSION + 1
    assert backup_path_for(env["db"]).is_file()
    assert env["errors"] == []


def test_open_storage_without_confirmer_shows_error(env: dict[str, Any]) -> None:
    """A caller with no confirmer gets the plain storage error. The error names the backup."""
    assert _open(env, restore=None) == STORAGE_ERROR_EXIT_CODE

    [message] = env["errors"]
    assert "newer version of InfinitePrep" in message
    assert str(backup_path_for(env["db"])) in message


def test_open_storage_without_backup_shows_error(env: dict[str, Any]) -> None:
    """With no usable backup the app offers no Restore. The storage error shows."""
    backup_path_for(env["db"]).unlink()

    assert _open(env, restore=True) == STORAGE_ERROR_EXIT_CODE

    assert env["prompts"] == []
    [message] = env["errors"]
    assert "newer version of InfinitePrep" in message


def test_restore_prompt_is_plain_text_with_quit_default(qtbot: Any) -> None:
    """The prompt shows paths literally, and Quit is the default button."""
    message = "Backup at <b>/music</b>/infiniteprep.sqlite3.pre-migration-backup"
    prompt = build_restore_prompt(message)
    qtbot.addWidget(prompt.box)

    assert prompt.box.textFormat() == Qt.TextFormat.PlainText
    assert prompt.box.text() == message
    assert prompt.box.defaultButton() is not prompt.restore_button
    assert prompt.restore_button.text() == RESTORE_LABEL


def test_restore_prompt_reports_the_clicked_button(qtbot: Any) -> None:
    """A click on Restore confirms. Any other button declines."""
    prompt = build_restore_prompt("message")
    qtbot.addWidget(prompt.box)
    assert prompt.confirmed() is False

    prompt.restore_button.click()

    assert prompt.confirmed() is True
