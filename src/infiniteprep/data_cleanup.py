"""Delete InfinitePrep's own data on request from Settings (D83a3b, D293 to D296).

The cleanup removes the folders the current storage locations name: the library and
settings, recovery backups, the waveform cache, and the downloaded fpcalc. A second
choice removes the saved API keys from the OS keyring. Music files and exports live
elsewhere and never appear in the list.

A folder is removed only when its path names InfinitePrep, or when it is a portable
Data folder whose marker still sits beside it. Any other folder is refused, so a
wrong location can never send the delete outside InfinitePrep's own folders.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from infiniteprep.config.credentials import CredentialStore, forget_all_credentials
from infiniteprep.storage_paths import (
    APP_NAME,
    MACOS_DATA_DIR_NAME,
    PORTABLE_DATA_DIR_NAME,
    PORTABLE_MARKER_NAME,
    StorageLocations,
)

logger = logging.getLogger(__name__)

APP_FOLDER_NAMES: Final[frozenset[str]] = frozenset({APP_NAME, MACOS_DATA_DIR_NAME})
REFUSED_REASON: Final[str] = "Not an InfinitePrep folder, so it was left in place."
NOT_A_FOLDER_REASON: Final[str] = "Not a plain folder, so it was left in place."


@dataclass(frozen=True)
class CleanupChoice:
    """What the person asked to delete. Both start unchecked (D294)."""

    delete_data: bool
    delete_keys: bool

    @property
    def any_selected(self) -> bool:
        """Return True when at least one item is chosen."""
        return self.delete_data or self.delete_keys


@dataclass
class CleanupReport:
    """What the cleanup removed and what it could not remove."""

    removed: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    keys_requested: bool = False
    keys_removed: bool = False

    @property
    def succeeded(self) -> bool:
        """Return True when every chosen item is gone."""
        return not self.failed and (self.keys_removed or not self.keys_requested)

    def describe(self) -> str:
        """Return a plain-text summary for the closing message."""
        lines = [f"Removed: {folder}" for folder in self.removed]
        lines += [f"Could not remove {folder}: {reason}" for folder, reason in self.failed]
        if self.keys_requested:
            lines.append(
                "Removed the saved API keys."
                if self.keys_removed
                else "Could not remove the saved API keys. Remove them in the system keyring."
            )
        if not lines:
            lines.append("Nothing needed removing.")
        return "\n".join(lines)


def data_folders(locations: StorageLocations) -> list[Path]:
    """Return the folders that hold InfinitePrep data, outermost first, without repeats.

    A folder inside another listed folder is dropped, because removing the outer one
    removes it too.
    """
    candidates = [
        locations.data_dir,
        locations.config_dir,
        locations.backup_dir,
        locations.bin_dir,
        locations.cache_dir,
    ]
    ordered = sorted(dict.fromkeys(candidates), key=lambda folder: len(folder.parts))
    chosen: list[Path] = []
    for folder in ordered:
        if not any(folder.is_relative_to(outer) for outer in chosen):
            chosen.append(folder)
    return chosen


def is_app_folder(folder: Path) -> bool:
    """Return True when folder is one of InfinitePrep's own folders."""
    if APP_FOLDER_NAMES.intersection(folder.parts):
        return True
    marker = folder.parent / PORTABLE_MARKER_NAME
    return folder.name == PORTABLE_DATA_DIR_NAME and marker.is_file()


def run_cleanup(
    choice: CleanupChoice,
    locations: StorageLocations,
    credential_store: CredentialStore | None = None,
) -> CleanupReport:
    """Delete the chosen items and report each result. One failure never stops the rest."""
    report = CleanupReport(keys_requested=choice.delete_keys)
    if choice.delete_data:
        for folder in data_folders(locations):
            _remove_folder(folder, report)
    if choice.delete_keys:
        report.keys_removed = forget_all_credentials(credential_store) == 0
    return report


def _remove_folder(folder: Path, report: CleanupReport) -> None:
    """Remove one folder, or record why it stayed."""
    if not is_app_folder(folder):
        report.failed.append((folder, REFUSED_REASON))
        return
    if not folder.exists() and not folder.is_symlink():
        return
    if folder.is_symlink() or not folder.is_dir():
        report.failed.append((folder, NOT_A_FOLDER_REASON))
        return
    try:
        shutil.rmtree(folder)
    except OSError as error:
        logger.warning("A data folder could not be removed: %s", error.strerror)
        report.failed.append((folder, error.strerror or error.__class__.__name__))
        return
    report.removed.append(folder)
