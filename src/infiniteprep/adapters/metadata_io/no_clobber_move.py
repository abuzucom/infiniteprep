"""Move a file to a new name without replacing a file that already holds that name."""

from __future__ import annotations

import ctypes
import errno
import functools
import logging
import os
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Final

from infiniteprep.hashing import compute_sha256

logger = logging.getLogger(__name__)

# renameat2 flag that makes the Linux kernel refuse an existing destination.
LINUX_RENAME_NOREPLACE: Final[int] = 1
# renameat2 resolves a relative path against the working directory with this descriptor.
LINUX_AT_FDCWD: Final[int] = -100
# renamex_np flag that makes macOS refuse an existing destination.
MACOS_RENAME_EXCL: Final[int] = 0x00000004
# A kernel or file system without the no-replace rename reports one of these codes.
UNSUPPORTED_RENAME_ERRNOS: Final[frozenset[int]] = frozenset(
    {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}
)

NoReplaceRename = Callable[[bytes, bytes], int]
# Device number and inode number of one file.
FileIdentity = tuple[int, int]
DISCARD_NAME_PREFIX: Final[str] = ".infiniteprep-discard-"


def file_identity(path: Path) -> FileIdentity:
    """Return the device and inode numbers of path without following a link."""
    info = os.lstat(path)
    return info.st_dev, info.st_ino


def discard_if_same_file(
    path: Path, identity: FileIdentity, expected_sha256: str | None = None
) -> bool:
    """Delete path only while it still is the file with identity, and keep any other file.

    A check followed by an unlink of the name can delete a file another program put there
    in between. The file first moves to a private name with the no-replace move. The
    identity check and the optional content check run on that private name, so an edit
    made in place before the move also keeps the file. A kept file moves back to path.
    """
    private = path.with_name(f"{DISCARD_NAME_PREFIX}{uuid.uuid4().hex}")
    try:
        move_to_new_path(path, private)
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("Kept %s, which could not move aside (%s).", path.name, type(exc).__name__)
        return False
    if _is_expected_file(private, identity, expected_sha256):
        private.unlink()
        return True
    try:
        move_to_new_path(private, path)
    except OSError as exc:
        logger.warning(
            "Kept another program's file at %s (%s). Rename it back to %s by hand.",
            private.name,
            type(exc).__name__,
            path.name,
        )
    return False


def _is_expected_file(path: Path, identity: FileIdentity, expected_sha256: str | None) -> bool:
    """Return True when path has identity and, if given, the expected SHA-256 digest."""
    if file_identity(path) != identity:
        return False
    return expected_sha256 is None or compute_sha256(path) == expected_sha256


def move_to_new_path(source: Path, destination: Path) -> None:
    """Move source to destination, or raise FileExistsError when destination exists.

    Each platform path makes one atomic call that fails on an existing destination. No
    existence check and no placeholder precede that call, so no file can slip in between.
    A system without such a call raises OSError, and the file keeps its old name.
    """
    try:
        _move_without_replacing(source, destination)
    except FileExistsError as exc:
        raise FileExistsError(
            f"Refusing to overwrite existing file {destination}; move or rename it and retry."
        ) from exc


def _move_without_replacing(source: Path, destination: Path) -> None:
    """Run the atomic no-replace move this platform offers, or refuse the move.

    A hard link followed by an unlink is no substitute. Another program can replace the
    new name between the two calls, and the unlink then drops the only copy of the file.
    """
    if sys.platform == "win32":
        # MoveFileEx without MOVEFILE_REPLACE_EXISTING refuses an existing destination.
        os.rename(source, destination)
        return
    if not _rename_without_replacing(source, destination):
        raise OSError(
            errno.ENOTSUP,
            "The system offers no move that refuses an existing file. The file keeps its "
            "old name. Rename it by hand",
            str(source),
        )


def _rename_without_replacing(source: Path, destination: Path) -> bool:
    """Run the kernel no-replace rename, or return False when the platform lacks it."""
    rename = native_no_replace_rename()
    if rename is None:
        return False
    if rename(os.fsencode(source), os.fsencode(destination)) == 0:
        return True
    code = ctypes.get_errno()
    if code in UNSUPPORTED_RENAME_ERRNOS:
        return False
    # OSError picks the matching subclass, so EEXIST raises FileExistsError.
    raise OSError(code, os.strerror(code), str(source), None, str(destination))


@functools.cache
def native_no_replace_rename() -> NoReplaceRename | None:
    """Return the C library no-replace rename for Linux or macOS, or None elsewhere."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None
    if sys.platform.startswith("linux"):
        return _linux_renameat2(libc)
    if sys.platform == "darwin":
        return _macos_renamex_np(libc)
    return None


def _linux_renameat2(libc: ctypes.CDLL) -> NoReplaceRename | None:
    """Bind renameat2 with RENAME_NOREPLACE, or return None for a C library without it."""
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        return None
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    return lambda source, destination: renameat2(
        LINUX_AT_FDCWD, source, LINUX_AT_FDCWD, destination, LINUX_RENAME_NOREPLACE
    )


def _macos_renamex_np(libc: ctypes.CDLL) -> NoReplaceRename | None:
    """Bind renamex_np with RENAME_EXCL, or return None for a system without it."""
    renamex_np = getattr(libc, "renamex_np", None)
    if renamex_np is None:
        return None
    renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex_np.restype = ctypes.c_int
    return lambda source, destination: renamex_np(source, destination, MACOS_RENAME_EXCL)
