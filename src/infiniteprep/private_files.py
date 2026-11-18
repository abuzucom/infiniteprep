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
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

PRIVATE_DIR_MODE: Final[int] = 0o700
PRIVATE_FILE_MODE: Final[int] = 0o600
# Group and other permission bits. A private file has none of them.
SHARED_ACCESS_BITS: Final[int] = 0o077
# Group and other write bits. A folder with either lets another account rename its files.
GROUP_OTHER_WRITE: Final[int] = 0o022
IS_WINDOWS: Final[bool] = os.name == "nt"
# O_NOFOLLOW makes the open fail on a link. O_NONBLOCK keeps a FIFO open from hanging.
NO_FOLLOW_FLAG: Final[int] = getattr(os, "O_NOFOLLOW", 0)
NON_BLOCK_FLAG: Final[int] = getattr(os, "O_NONBLOCK", 0)
STAGING_DIR_PREFIX: Final[str] = ".infiniteprep-staging-"
ROOT_UID: Final[int] = 0
# Linux stops a lookup after 40 links with ELOOP. The manual walk uses the same bound.
MAX_LINK_HOPS: Final[int] = 40


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


def resolve_library_path(library_path: Path) -> Path:
    """Return library_path with every link in its folder resolved. The file name stays as is.

    The app resolves the folder once and then opens the real path. A link above the library
    that changes later no longer redirects the library, its side files, or its backups.
    refuse_symlink covers a link at the file name itself.
    """
    return library_path.parent.resolve(strict=True) / library_path.name


def warn_if_folder_is_shared(folder: Path) -> bool:
    """Log a warning when another account can redirect or change files in folder on POSIX.

    The check covers folder itself and every folder that the name lookup passes, the folders
    that hold each link on the way included. A folder that another account owns or can write
    lets that account swap a name between two opens. Above the library, root ownership and
    the sticky bit of folders such as /tmp count as safe. A portable Data folder on FAT or
    exFAT media shows shared modes. The app still opens the library there.
    Returns whether any folder is shared.
    """
    if IS_WINDOWS:
        return False
    library_folder = folder.resolve(strict=True)
    shared = False
    for path in dict.fromkeys(_traversed_folders(folder)):
        is_library_folder = path == library_folder
        if _is_trusted_folder(path.lstat(), is_library_folder=is_library_folder):
            continue
        shared = True
        _warn_shared_folder(path, is_library_folder=is_library_folder)
    return shared


def _is_trusted_folder(status: os.stat_result, *, is_library_folder: bool) -> bool:
    """Return whether no other account can rename or add names in the folder."""
    mode = stat.S_IMODE(status.st_mode)
    if is_library_folder:
        return status.st_uid == os.getuid() and not mode & GROUP_OTHER_WRITE
    trusted_owner = status.st_uid in (os.getuid(), ROOT_UID)
    # The sticky bit stops other accounts from renaming or removing entries they do not own.
    return trusted_owner and (not mode & GROUP_OTHER_WRITE or bool(mode & stat.S_ISVTX))


def _warn_shared_folder(path: Path, *, is_library_folder: bool) -> None:
    """Log the shared folder warning for the library folder or a folder above it."""
    if is_library_folder:
        logger.warning(
            "Other accounts can change files in the library folder %s. Run chmod 700 on it as "
            "its owner, or move the library to a folder only this account can change.",
            path.name,
        )
        return
    logger.warning(
        "Other accounts can change the folder %s above the library and redirect it. Remove "
        "group and other write access from that folder, or move the library elsewhere.",
        path,
    )


def _traversed_folders(folder: Path) -> list[Path]:
    """Return each real folder whose entries the lookup of folder reads, in lookup order.

    The walk follows each link by hand. The folder that holds a link therefore joins the
    list, as do the folders of its target. MAX_LINK_HOPS bounds a link cycle.
    """
    absolute = Path(os.path.abspath(folder))
    current = Path(absolute.anchor)
    visited = [current]
    pending = deque(absolute.parts[1:])
    hops = 0
    while pending:
        name = pending.popleft()
        if name in ("", "."):
            continue
        if name == "..":
            current = current.parent
            visited.append(current)
            continue
        candidate = current / name
        if not candidate.is_symlink():
            current = candidate
            visited.append(current)
            continue
        hops += 1
        if hops > MAX_LINK_HOPS:
            raise OSError(errno.ELOOP, "Too many links in the library path", str(folder))
        target = Path(os.readlink(candidate))
        if target.is_absolute():
            current = Path(target.anchor)
            visited.append(current)
        pending.extendleft(reversed(target.parts[1:] if target.is_absolute() else target.parts))
    return visited


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
    """Raise PermissionError unless folder is a real folder. Warn when others can use it.

    FAT and exFAT media ignore modes and report one owner for every file. A portable library
    there still gets its backups. A warning names the exposure instead of a failure.
    """
    if IS_WINDOWS:
        return
    status = folder.lstat()
    if not stat.S_ISDIR(status.st_mode):
        raise PermissionError(
            f"The staging folder {folder} is not a real folder. Check the library folder "
            "for links and start again."
        )
    owned = status.st_uid == os.getuid()
    private = not stat.S_IMODE(status.st_mode) & SHARED_ACCESS_BITS
    if owned and private:
        return
    logger.warning(
        "Other accounts can change files in the staging folder %s. The media ignore folder "
        "permissions. Keep the library on a drive only this account can reach.",
        folder.name,
    )


def _remove_staging_dir(staging: Path) -> None:
    """Delete the files in staging and then staging itself. A failure only logs.

    A link under the staging name loses only the link. Its target keeps every file.
    """
    try:
        if staging.is_symlink():
            staging.unlink()
            return
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
