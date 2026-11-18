"""Owner-only folders and files for the library, its backups, and the settings.

The library holds file paths, tag history, and review notes. Another account on a shared
machine has no reason to read it. POSIX mode 0700 on new folders and 0600 on files keep
other accounts out. Windows ignores these bits. Its per-user profile ACL applies instead.
"""

from __future__ import annotations

import errno
import logging
import os
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

PRIVATE_DIR_MODE: Final[int] = 0o700
PRIVATE_FILE_MODE: Final[int] = 0o600
# Group and other permission bits. A private file has none of them.
SHARED_ACCESS_BITS: Final[int] = 0o077
IS_WINDOWS: Final[bool] = os.name == "nt"
# O_NOFOLLOW makes the open fail on a link. O_NONBLOCK keeps a FIFO open from hanging.
NO_FOLLOW_FLAG: Final[int] = getattr(os, "O_NOFOLLOW", 0)
NON_BLOCK_FLAG: Final[int] = getattr(os, "O_NONBLOCK", 0)
STAGING_DIR_PREFIX: Final[str] = ".infiniteprep-staging-"


def create_private_dirs(folder: Path) -> None:
    """Create folder and each missing parent with mode 0700.

    A folder that already exists keeps its mode. A user can choose a shared parent. The app
    must not change it.
    """
    missing: list[Path] = []
    current = folder
    # Each step moves one level up. The walk therefore ends at the filesystem root.
    while not current.exists() and current.parent != current:
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir(mode=PRIVATE_DIR_MODE, exist_ok=True)
    # A file under the folder name fails here. mkdir with exist_ok fails on it the same way.
    if not folder.is_dir():
        raise NotADirectoryError(
            errno.ENOTDIR, "Not a folder. Free the name and retry", str(folder)
        )


def refuse_symlink(path: Path) -> None:
    """Raise OSError when path is a symbolic link.

    SQLite opens a path by name and follows a link. The check keeps the library and its side
    files inside the folder the app chose.
    """
    if path.is_symlink():
        raise OSError(
            f"The library at {path} is a symbolic link. Move the real library file to that "
            "name and start again."
        )


@contextmanager
def private_staging_dir(parent: Path) -> Iterator[Path]:
    """Yield a new owner-only folder inside parent. Remove it and its files afterwards.

    Another account cannot add, rename, or replace a name inside a 0700 folder. A file
    staged there keeps its identity between its create and every reopen by name.
    """
    staging = parent / f"{STAGING_DIR_PREFIX}{uuid.uuid4().hex}"
    # mkdir without exist_ok fails on any existing name, a planted link included.
    staging.mkdir(mode=PRIVATE_DIR_MODE)
    try:
        _require_owner_only_dir(staging)
        yield staging
    finally:
        _remove_staging_dir(staging)


def _require_owner_only_dir(folder: Path) -> None:
    """Raise PermissionError unless folder is a real folder that only this account can use."""
    if IS_WINDOWS:
        return
    status = folder.lstat()
    owned = status.st_uid == os.getuid()
    private = not stat.S_IMODE(status.st_mode) & SHARED_ACCESS_BITS
    if not (stat.S_ISDIR(status.st_mode) and owned and private):
        raise PermissionError(
            f"The staging folder {folder} is not private to this account. Check the "
            "permissions of the library folder and start again."
        )


def _remove_staging_dir(staging: Path) -> None:
    """Delete the files in staging and then staging itself. A failure only logs."""
    try:
        leftovers = list(staging.iterdir())
        for path in leftovers:
            path.unlink(missing_ok=True)
        staging.rmdir()
    except OSError as error:
        logger.warning("Could not remove the staging folder %s: %s", staging.name, error.strerror)


def ensure_private_file(path: Path) -> None:
    """Create path as an empty file with mode 0600, or remove shared access from it.

    The exclusive create refuses any existing name. A planted link fails it.
    """
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
    except FileExistsError:
        restrict_to_owner(path)
        return
    os.close(descriptor)


def create_new_private_file(path: Path) -> None:
    """Create path as an empty file with mode 0600. Any existing name raises FileExistsError."""
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE))


def restrict_to_owner(path: Path) -> None:
    """Remove group and other access from an existing regular file on POSIX.

    A link, folder, or device under the name keeps its mode. The open that follows reports
    a problem with it. A file owned by another account keeps its mode. A warning names the fix.
    """
    if IS_WINDOWS:
        return
    status = path.lstat()
    if not stat.S_ISREG(status.st_mode) or not stat.S_IMODE(status.st_mode) & SHARED_ACCESS_BITS:
        return
    # The chmod goes through a descriptor. A link swapped in after the lstat fails the open.
    try:
        descriptor = os.open(path, os.O_RDONLY | NO_FOLLOW_FLAG | NON_BLOCK_FLAG)
    except OSError as error:
        logger.warning("Could not open %s to restrict it: %s", path.name, error.strerror)
        return
    try:
        _restrict_descriptor(descriptor, status, path.name)
    finally:
        os.close(descriptor)


def _restrict_descriptor(descriptor: int, expected: os.stat_result, name: str) -> None:
    """Drop shared access through descriptor when it still names the checked file."""
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
        logger.warning("%s changed during the permission check. Its mode stays.", name)
        return
    try:
        os.fchmod(descriptor, stat.S_IMODE(opened.st_mode) & ~SHARED_ACCESS_BITS)
    except PermissionError:
        logger.warning("Other accounts can read %s. Run chmod 600 on it as its owner.", name)
