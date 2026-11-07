"""Where InfinitePrep keeps its database, settings, caches, backups, and tools.

A packaged build with a portable.mode marker beside its executable keeps the
database and settings in a Data folder there (D261). A Linux AppImage always
keeps them in an InfinitePrep-Data folder beside the .AppImage file (D280 to
D282). Caches, recovery backups, and the downloaded fpcalc stay per-user in
every mode (D260, D281). Every other run uses the per-user folders from
platformdirs.

main() resolves the locations once at startup and calls configure(). Defaults
elsewhere read current(), which falls back to the per-user folders when nothing
was configured, as in tests (D263).
"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
from collections.abc import Callable, Mapping
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
# An AppImage is one file, often in a shared folder such as ~/Downloads, so its
# data folder carries the app name (D282).
APPIMAGE_DATA_DIR_NAME: Final[str] = "InfinitePrep-Data"
# The AppImage runtime sets these for the process it starts.
APPIMAGE_ENV_VAR: Final[str] = "APPIMAGE"
APPDIR_ENV_VAR: Final[str] = "APPDIR"
WRITE_PROBE_PREFIX: Final[str] = ".write-probe-"
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


@dataclass(frozen=True)
class AppImageLaunch:
    """Where the AppImage runtime says the .AppImage file and its mount are."""

    image: Path
    appdir: Path


ConfirmFallback = Callable[[Path], FallbackAnswer]


def appimage_launch_from_environment(environ: Mapping[str, str]) -> AppImageLaunch | None:
    """Return the AppImage launch details, or None when either value is unset.

    resolve_locations() still checks the values before it trusts them.
    """
    image = environ.get(APPIMAGE_ENV_VAR, "")
    appdir = environ.get(APPDIR_ENV_VAR, "")
    if not image or not appdir:
        return None
    return AppImageLaunch(image=Path(image), appdir=Path(appdir))


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
    """Create folder if needed and prove it accepts a new file.

    The probe gets a unique temporary name and deletes itself, so it never touches a
    file already in the folder.
    """
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=WRITE_PROBE_PREFIX):
            pass
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


def _appimage_folder(executable: Path, launch: AppImageLaunch | None) -> Path | None:
    """Return the folder holding the running .AppImage, or None when this is no AppImage.

    Child processes inherit APPIMAGE and APPDIR. The executable must lie inside APPDIR,
    so a tar.gz copy started from inside another AppImage never takes its folder.
    """
    if launch is None or not launch.image.is_absolute() or not launch.appdir.is_absolute():
        return None
    if not launch.image.is_file():
        return None
    if not executable.resolve().is_relative_to(launch.appdir.resolve()):
        return None
    return launch.image.resolve().parent


def _adjacent_data_dir(
    executable: Path, appimage: AppImageLaunch | None
) -> tuple[Path, Path] | None:
    """Return the app folder and its data folder, or None when the run is per-user.

    The AppImage comes first, because a marker inside its read-only mount would point
    at a folder that cannot be written.
    """
    appimage_folder = _appimage_folder(executable, appimage)
    if appimage_folder is not None:
        return appimage_folder, appimage_folder / APPIMAGE_DATA_DIR_NAME
    app_folder = executable.resolve().parent
    if (app_folder / PORTABLE_MARKER_NAME).is_file():
        return app_folder, app_folder / PORTABLE_DATA_DIR_NAME
    return None


def resolve_locations(
    executable: Path,
    *,
    frozen: bool,
    confirm_fallback: ConfirmFallback,
    appimage: AppImageLaunch | None = None,
) -> StorageLocations | None:
    """Return the storage folders for this run, or None when the person chooses Quit.

    Only a packaged build looks for an AppImage or the marker, so running from source
    never turns portable. Once the data folder can be written again, a remembered
    fallback for this folder is forgotten (D259).
    """
    adjacent = _adjacent_data_dir(executable, appimage) if frozen else None
    if adjacent is None:
        return per_user_locations()
    app_folder, portable_dir = adjacent
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
