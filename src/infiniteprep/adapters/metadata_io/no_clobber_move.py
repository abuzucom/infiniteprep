"""Move a file to a new name without replacing a file that already holds that name."""

from __future__ import annotations

import ctypes
import errno
import functools
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

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
# A file system without hard links, such as FAT, reports one of these codes from link.
UNSUPPORTED_LINK_ERRNOS: Final[frozenset[int]] = frozenset(
    {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK}
)

NoReplaceRename = Callable[[bytes, bytes], int]


def move_to_new_path(source: Path, destination: Path) -> None:
    """Move source to destination, or raise FileExistsError when destination exists.

    Each platform path makes one atomic call that fails on an existing destination. No
    existence check and no placeholder precede that call, so no file can slip in between.
    """
    try:
        _move_without_replacing(source, destination)
    except FileExistsError as exc:
        raise FileExistsError(
            f"Refusing to overwrite existing file {destination}; move or rename it and retry."
        ) from exc


def _move_without_replacing(source: Path, destination: Path) -> None:
    """Pick the atomic no-replace move this platform offers."""
    if sys.platform == "win32":
        # MoveFileEx without MOVEFILE_REPLACE_EXISTING refuses an existing destination.
        os.rename(source, destination)
        return
    if _rename_without_replacing(source, destination):
        return
    _link_then_unlink(source, destination)


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


def _link_then_unlink(source: Path, destination: Path) -> None:
    """Move through a hard link, which fails atomically on an existing destination."""
    try:
        os.link(source, destination)
    except OSError as exc:
        if exc.errno in UNSUPPORTED_LINK_ERRNOS:
            raise OSError(
                exc.errno,
                "The file system offers no move that refuses an existing file. The file "
                "keeps its old name. Rename it by hand",
                str(source),
            ) from exc
        raise
    try:
        os.unlink(source)
    except OSError:
        # The link call created destination, so the extra name belongs to this move.
        destination.unlink(missing_ok=True)
        raise
