"""Where InfinitePrep keeps its database, settings, caches, backups, and tools.

A packaged build with a portable.mode marker beside its executable keeps the
database and settings in a Data folder there (D261). Caches, recovery backups,
and the downloaded fpcalc stay per-user in every mode (D260). Every other run
uses the per-user folders from platformdirs.

main() resolves the locations once at startup and calls configure(). Defaults
elsewhere read current(), which falls back to the per-user folders when nothing
was configured, as in tests (D263).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

import platformdirs

logger = logging.getLogger(__name__)

APP_NAME: Final[str] = "InfinitePrep"
APP_AUTHOR: Final[str] = "InfinitePrep"
PORTABLE_MARKER_NAME: Final[str] = "portable.mode"
PORTABLE_DATA_DIR_NAME: Final[str] = "Data"
WRITE_PROBE_NAME: Final[str] = ".write-probe"
FALLBACK_LIST_NAME: Final[str] = "storage_fallback.json"
BACKUP_DIR_NAME: Final[str] = "backups"
BIN_DIR_NAME: Final[str] = "bin"


class StorageMode(StrEnum):
    """How the locations were chosen."""

    PER_USER = "per_user"
    PORTABLE = "portable"
    PER_USER_FALLBACK = "per_user_fallback"


@dataclass(frozen=True)
class StorageLocations:
    """Folders for each kind of data the app keeps."""

    data_dir: Path
    config_dir: Path
    cache_dir: Path
    backup_dir: Path
    bin_dir: Path
    mode: StorageMode


@dataclass(frozen=True)
class FallbackAnswer:
    """The answer to the unwritable-folder prompt."""

    use_per_user: bool
    remember: bool


ConfirmFallback = Callable[[Path], FallbackAnswer]


def per_user_locations(mode: StorageMode = StorageMode.PER_USER) -> StorageLocations:
    """Return the per-user folders the app has always used."""
    data_dir = Path(platformdirs.user_data_dir(APP_NAME, APP_AUTHOR))
    return StorageLocations(
        data_dir=data_dir,
        config_dir=Path(platformdirs.user_config_dir(APP_NAME, APP_AUTHOR)),
        cache_dir=Path(platformdirs.user_cache_dir(APP_NAME)),
        backup_dir=data_dir / BACKUP_DIR_NAME,
        bin_dir=data_dir / BIN_DIR_NAME,
        mode=mode,
    )


def _portable_locations(portable_dir: Path) -> StorageLocations:
    """Return portable folders: database and settings in Data, the rest per-user."""
    per_user = per_user_locations()
    return StorageLocations(
        data_dir=portable_dir,
        config_dir=portable_dir,
        cache_dir=per_user.cache_dir,
        backup_dir=per_user.backup_dir,
        bin_dir=per_user.bin_dir,
        mode=StorageMode.PORTABLE,
    )


def _is_writable_folder(folder: Path) -> bool:
    """Create folder if needed and prove it accepts a new file."""
    probe = folder / WRITE_PROBE_NAME
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as error:
        logger.info("Portable Data folder is not writable: %s", error.strerror)
        return False
    return True


def _fallback_list_path() -> Path:
    """Return the per-user file that lists app folders using the per-user fallback."""
    return per_user_locations().config_dir / FALLBACK_LIST_NAME


def _read_fallback_folders() -> set[str]:
    """Return the remembered app folders, or an empty set when none can be read."""
    try:
        raw = json.loads(_fallback_list_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except (OSError, ValueError) as error:
        logger.warning("Ignoring the storage fallback list: %s", error)
        return set()
    if not isinstance(raw, list):
        return set()
    return {entry for entry in raw if isinstance(entry, str)}


def _write_fallback_folders(folders: set[str]) -> None:
    """Save the remembered app folders, or remove the file when none remain."""
    path = _fallback_list_path()
    if not folders:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(folders), indent=2), encoding="utf-8")


def _resolve_unwritable(
    app_folder: Path, confirm_fallback: ConfirmFallback
) -> StorageLocations | None:
    """Use the remembered fallback, or ask. Return None when the person chooses Quit."""
    key = str(app_folder)
    if key in _read_fallback_folders():
        return per_user_locations(StorageMode.PER_USER_FALLBACK)
    answer = confirm_fallback(app_folder)
    if not answer.use_per_user:
        return None
    if answer.remember:
        _write_fallback_folders(_read_fallback_folders() | {key})
    return per_user_locations(StorageMode.PER_USER_FALLBACK)


def resolve_locations(
    executable: Path, *, frozen: bool, confirm_fallback: ConfirmFallback
) -> StorageLocations | None:
    """Return the storage folders for this run, or None when the person chooses Quit.

    Only a packaged build looks for the marker, so running from source never turns
    portable. Once the Data folder can be written again, a remembered fallback for this
    folder is forgotten (D259).
    """
    app_folder = executable.resolve().parent
    if not frozen or not (app_folder / PORTABLE_MARKER_NAME).is_file():
        return per_user_locations()
    portable_dir = app_folder / PORTABLE_DATA_DIR_NAME
    if not _is_writable_folder(portable_dir):
        return _resolve_unwritable(app_folder, confirm_fallback)
    remembered = _read_fallback_folders()
    if str(app_folder) in remembered:
        _write_fallback_folders(remembered - {str(app_folder)})
    return _portable_locations(portable_dir)


class StorageRegistry:
    """Holds the locations chosen at startup. They can be set once only."""

    def __init__(self) -> None:
        """Start with no locations set."""
        self._locations: StorageLocations | None = None
        self._lock = threading.Lock()

    def configure(self, locations: StorageLocations) -> None:
        """Set the locations. A second call raises RuntimeError."""
        with self._lock:
            if self._locations is not None:
                raise RuntimeError("Storage locations are already set. Configure them once.")
            self._locations = locations

    def current(self) -> StorageLocations:
        """Return the configured locations, or the per-user folders when none are set."""
        with self._lock:
            locations = self._locations
        return locations or per_user_locations()


# The process-wide registry. Tests swap in a fresh one to stay isolated.
STORAGE_REGISTRY = StorageRegistry()


def configure(locations: StorageLocations) -> None:
    """Set the locations for this process. A second call raises RuntimeError."""
    STORAGE_REGISTRY.configure(locations)


def current() -> StorageLocations:
    """Return the configured locations, or the per-user folders when none are set."""
    return STORAGE_REGISTRY.current()
