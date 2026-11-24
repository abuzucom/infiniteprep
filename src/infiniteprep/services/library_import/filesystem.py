"""Safe folder and file reading for the library import (D333).

A scan of the user profile must not follow a link out of the folders the DJ
chose. A symbolic link and a Windows junction both leave the folder, so both
are refused. A file inside the chosen root must also stay there while it is
read, so the file is opened once and its identity checked before and after
the read.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

logger = logging.getLogger(__name__)

# The open flags that refuse a symbolic link at the last part of the path.
# The flag is missing on Windows, so the identity check covers that case.
NOFOLLOW_FLAG: Final[int] = getattr(os, "O_NOFOLLOW", 0)
BINARY_FLAG: Final[int] = getattr(os, "O_BINARY", 0)


@dataclass(frozen=True)
class FileIdentity:
    """The device and the inode that name one file.

    The pair changes when the file at a path is replaced by another one.
    """

    device: int
    inode: int


@dataclass(frozen=True)
class OpenedFile:
    """A file opened for reading, with the identity it had when it opened.

    The handle closes when the block ends, so a long import never holds more
    than one open file.
    """

    path: Path
    handle: IO[bytes]
    identity: FileIdentity

    def __enter__(self) -> OpenedFile:
        """Return the open file for the block."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the file when the block ends."""
        self.handle.close()


def is_link_or_junction(path: Path) -> bool:
    """Return True when the path is a symbolic link, a junction, or a reparse point."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        link_stat = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(link_stat, "st_reparse_tag", 0))


def real_children(parent: Path) -> list[Path]:
    """Return the real subfolders of a parent folder.

    A link out of the parent folder is left out. A folder that cannot be read
    is left out too, and the rest of the parent still scans.
    """
    try:
        children = sorted(parent.iterdir())
    except OSError:
        logger.warning("Library import skipped one unreadable folder.")
        return []
    return [child for child in children if child.is_dir() and not is_link_or_junction(child)]


def file_identity(path: Path) -> FileIdentity | None:
    """Return the identity of the file a path names, or None when it cannot be read."""
    try:
        info = path.stat()
    except OSError:
        return None
    return FileIdentity(device=info.st_dev, inode=info.st_ino)


def link_identity(path: Path) -> FileIdentity | None:
    """Return the identity of the directory entry, without following a link.

    A path that names a link gets the identity of the link itself. The open
    of that path gets the identity of the file the link points at, so the two
    differ and the open is refused.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return FileIdentity(device=info.st_dev, inode=info.st_ino)


def resolve_inside_root(candidate: Path, root: Path) -> Path | None:
    """Return the resolved candidate when it stays inside the resolved root.

    The resolved paths settle the question a text comparison cannot. A folder
    inside the root that points elsewhere carries the path out of it, so every
    folder of the path below the root is checked as well.
    """
    if not root.is_absolute():
        return None
    try:
        resolved_root = root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
    except OSError:
        return None
    if resolved_candidate != resolved_root:
        try:
            resolved_candidate.relative_to(resolved_root)
        except ValueError:
            return None
    if _crosses_link(candidate, root):
        return None
    return resolved_candidate


def open_inside_root(candidate: Path, root: Path) -> OpenedFile | None:
    """Open a file for reading, refusing a path that leaves the root.

    The file opens once. The identity of the open file is compared with the
    identity of the directory entry, which no link follows, so a file that a
    link replaced is refused rather than read. The path is checked against
    the root again after the open, because the flag that refuses a link at
    the last part of the path is missing on Windows, so a folder swapped for
    a junction between the check and the open still reaches a file outside
    the root.
    """
    resolved = resolve_inside_root(candidate, root)
    if resolved is None:
        return None
    expected = link_identity(resolved)
    if expected is None:
        return None
    try:
        descriptor = os.open(resolved, os.O_RDONLY | NOFOLLOW_FLAG | BINARY_FLAG)
    except OSError:
        logger.warning("Import skipped one file after an unreadable open.")
        return None
    handle = os.fdopen(descriptor, "rb")
    opened = _identity_of_handle(handle)
    if opened != expected:
        handle.close()
        logger.warning("Import skipped one file that changed while it opened.")
        return None
    if resolve_inside_root(candidate, root) != resolved:
        handle.close()
        logger.warning("Import skipped one file whose path left the root.")
        return None
    return OpenedFile(path=resolved, handle=handle, identity=opened)


def identity_still_matches(opened: OpenedFile) -> bool:
    """Return True when the path still names the file the handle holds.

    A file that was replaced while it was read fails the check, so the facts
    of the replacement never reach the app.
    """
    return file_identity(opened.path) == opened.identity


def _identity_of_handle(handle: IO[bytes]) -> FileIdentity:
    """Return the identity of the file an open handle holds."""
    info = os.fstat(handle.fileno())
    return FileIdentity(device=info.st_dev, inode=info.st_ino)


def _crosses_link(candidate: Path, root: Path) -> bool:
    """Return True when a folder of the path below the root is a link."""
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        current = current / part
        if is_link_or_junction(current):
            return True
    return False
