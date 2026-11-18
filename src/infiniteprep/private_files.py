"""Owner-only folders and files for the library, its backups, and the settings.

The library holds file paths, tag history, and review notes. Another account on a shared
machine has no reason to read it. POSIX mode 0700 on new folders and 0600 on files keep
other accounts out. Windows ignores these bits. Its per-user profile ACL applies instead.
"""

from __future__ import annotations

import logging
import os
import stat
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


def create_private_dirs(folder: Path) -> None:
    """Create folder and each missing parent with mode 0700.

    A folder that already exists keeps its mode. A user can choose a shared parent, and the
    app must not change it.
    """
    missing: list[Path] = []
    current = folder
    # Each step moves one level up, so the walk ends at the filesystem root.
    while not current.exists() and current.parent != current:
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir(mode=PRIVATE_DIR_MODE, exist_ok=True)


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
