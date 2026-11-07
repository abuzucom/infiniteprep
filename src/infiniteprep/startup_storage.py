"""Open the app's storage at startup, or report why it cannot be opened."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from infiniteprep import storage_paths
from infiniteprep.db.connection import DatabaseManager
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

ShowError = Callable[[str], None]


@dataclass(frozen=True)
class OpenedStorage:
    """The chosen folders and the repository opened in them."""

    locations: StorageLocations
    repository: Repository


def describe_storage_error(error: OSError | sqlite3.Error) -> str:
    """Return the plain-text message shown when storage cannot open."""
    detail = str(error).replace("\r", " ").replace("\n", " ")
    return STORAGE_ERROR_TEMPLATE.format(detail=detail)


def open_storage(
    executable: Path,
    *,
    frozen: bool,
    confirm_fallback: ConfirmFallback,
    show_error: ShowError,
    appimage: AppImageLaunch | None = None,
) -> OpenedStorage | int:
    """Choose the folders, open the database, and set the process-wide locations.

    Returns the opened storage, or an exit code: QUIT_EXIT_CODE after Quit in the
    read-only prompt, STORAGE_ERROR_EXIT_CODE after a folder or database error. The
    locations are set only after the database opens, so a failed start leaves them unset.
    """
    try:
        locations = resolve_locations(
            executable, frozen=frozen, confirm_fallback=confirm_fallback, appimage=appimage
        )
        if locations is None:
            return QUIT_EXIT_CODE
        locations.data_dir.mkdir(parents=True, exist_ok=True)
        repository = Repository(DatabaseManager(locations.data_dir / DATABASE_FILE_NAME))
    except (OSError, sqlite3.Error) as error:
        logger.exception("Storage could not open at startup.")
        show_error(describe_storage_error(error))
        return STORAGE_ERROR_EXIT_CODE
    storage_paths.configure(locations)
    return OpenedStorage(locations, repository)
