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

    The exclusive create refuses an existing name, so a planted link is never followed here.
    """
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
    except FileExistsError:
        restrict_to_owner(path)
        return
    os.close(descriptor)


def restrict_to_owner(path: Path) -> None:
    """Remove group and other access from an existing file on POSIX.

    A file owned by another account keeps its mode. The app still opens it, and the warning
    names the fix.
    """
    if IS_WINDOWS:
        return
    status = path.stat()
    current_mode = stat.S_IMODE(status.st_mode)
    # A folder or device under the name stays as it is. The open that follows reports it.
    if not stat.S_ISREG(status.st_mode) or not current_mode & SHARED_ACCESS_BITS:
        return
    try:
        path.chmod(current_mode & ~SHARED_ACCESS_BITS)
    except PermissionError:
        logger.warning("Other accounts can read %s. Run chmod 600 on it as its owner.", path.name)
