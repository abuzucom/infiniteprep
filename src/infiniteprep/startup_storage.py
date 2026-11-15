"""Open the app's storage at startup, or report why it cannot be opened."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from infiniteprep import storage_paths
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations.backup import newer_copy_path_for, restore_pre_migration_backup
from infiniteprep.db.migrations.runner import NewerLibraryError
from infiniteprep.db.repositories import Repository
from infiniteprep.storage_paths import (
    AppImageLaunch,
    ConfirmFallback,
    StorageLocations,
    resolve_locations,
)

logger = logging.getLogger(__name__)

DATABASE_FILE_NAME: Final[str] = "infiniteprep.sqlite3"
QUIT_EXIT_CODE: Final[int] = 0
STORAGE_ERROR_EXIT_CODE: Final[int] = 1
STORAGE_ERROR_TEMPLATE: Final[str] = (
    "InfinitePrep cannot open its library folder. {detail} "
    "Check that your account can write to the folder, then start InfinitePrep again."
)
NEWER_LIBRARY_TEMPLATE: Final[str] = (
    "A newer version of InfinitePrep saved this library. The library is at version "
    "{library_version}. This app opens versions up to {known_version}. Install the newer "
    "version to keep using this library."
)
BACKUP_BY_HAND_TEMPLATE: Final[str] = (
    " A backup from before the last update is at {backup}. Close InfinitePrep and put that "
    "file in place of the library to go back to it."
)
RESTORE_OFFER_TEMPLATE: Final[str] = (
    " A backup from before the last update is at {backup}. Restore puts that backup in place "
    "and keeps the current library beside it as {kept}. Changes made with the newer version "
    "stay only in that copy. Quit closes InfinitePrep and changes nothing."
)

ShowError = Callable[[str], None]
ConfirmRestore = Callable[[str], bool]


@dataclass(frozen=True)
class OpenedStorage:
    """The chosen folders and the repository opened in them."""

    locations: StorageLocations
    repository: Repository


def describe_storage_error(error: OSError | sqlite3.Error) -> str:
    """Return the plain-text message shown when storage cannot open."""
    detail = str(error).replace("\r", " ").replace("\n", " ")
    return STORAGE_ERROR_TEMPLATE.format(detail=detail)


def describe_newer_library(error: NewerLibraryError) -> str:
    """Return the plain-text message for a library from a newer version, with no Restore."""
    message = NEWER_LIBRARY_TEMPLATE.format(
        library_version=error.library_version, known_version=error.known_version
    )
    if error.backup_path is None:
        return message
    return message + BACKUP_BY_HAND_TEMPLATE.format(backup=error.backup_path)


def describe_restore_offer(error: NewerLibraryError, kept: Path) -> str:
    """Return the plain-text message for the prompt that offers Restore."""
    message = NEWER_LIBRARY_TEMPLATE.format(
        library_version=error.library_version, known_version=error.known_version
    )
    return message + RESTORE_OFFER_TEMPLATE.format(backup=error.backup_path, kept=kept)


def _restore_and_open(
    db_path: Path, error: NewerLibraryError, confirm_restore: ConfirmRestore
) -> Repository | None:
    """Offer Restore. Return the reopened repository, or None after Quit."""
    restore_time = datetime.now(UTC)
    kept = newer_copy_path_for(db_path, restore_time)
    if not confirm_restore(describe_restore_offer(error, kept)):
        return None
    restore_pre_migration_backup(db_path, now=restore_time)
    logger.info("Restored the pre-migration library backup at startup.")
    return Repository(DatabaseManager(db_path))


def _open_repository(db_path: Path, confirm_restore: ConfirmRestore | None) -> Repository | None:
    """Open the library. Offer Restore for a newer library when a usable backup exists."""
    try:
        return Repository(DatabaseManager(db_path))
    except NewerLibraryError as error:
        if confirm_restore is None or error.backup_path is None:
            raise
        return _restore_and_open(db_path, error, confirm_restore)


def open_storage(
    executable: Path,
    *,
    frozen: bool,
    confirm_fallback: ConfirmFallback,
    show_error: ShowError,
    appimage: AppImageLaunch | None = None,
    confirm_restore: ConfirmRestore | None = None,
) -> OpenedStorage | int:
    """Choose the folders, open the database, and set the process-wide locations.

    Returns the opened storage, or an exit code: QUIT_EXIT_CODE after Quit in the
    read-only prompt or the Restore prompt, STORAGE_ERROR_EXIT_CODE after a folder or
    database error. The locations are set only after the database opens, so a failed
    start leaves them unset. Without confirm_restore, a newer library shows an error.
    """
    try:
        locations = resolve_locations(
            executable, frozen=frozen, confirm_fallback=confirm_fallback, appimage=appimage
        )
        if locations is None:
            return QUIT_EXIT_CODE
        locations.data_dir.mkdir(parents=True, exist_ok=True)
        repository = _open_repository(locations.data_dir / DATABASE_FILE_NAME, confirm_restore)
        if repository is None:
            return QUIT_EXIT_CODE
    except NewerLibraryError as error:
        logger.warning("The library comes from a newer version and was not opened.")
        show_error(describe_newer_library(error))
        return STORAGE_ERROR_EXIT_CODE
    except (OSError, sqlite3.Error) as error:
        logger.exception("Storage could not open at startup.")
        show_error(describe_storage_error(error))
        return STORAGE_ERROR_EXIT_CODE
    storage_paths.configure(locations)
    return OpenedStorage(locations, repository)
