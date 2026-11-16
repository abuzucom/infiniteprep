"""Shared helpers for USB drive inspectors (USB-1 #153, USB-2 #154).

Untrusted metadata sanitization and safe subprocess execution shared by the
platform inspectors. Every helper treats external command output as untrusted
input and never executes reconstructed text.
"""

from __future__ import annotations

import contextlib
import ctypes
import importlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

DEFAULT_COMMAND_TIMEOUT_SECONDS: Final[float] = 15.0
MAX_METADATA_STR_LEN: Final[int] = 512
MAX_TEMP_ATTEMPTS: Final[int] = 16
MAX_USB_JSON_BYTES: Final[int] = 16 * 1024 * 1024
ANSI_ESCAPE_RE: Final[re.Pattern[str]] = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

_WIN32_GENERIC_READ: Final[int] = 0x80000000
_WIN32_DELETE: Final[int] = 0x00010000
_WIN32_FILE_READ_ATTRIBUTES: Final[int] = 0x0080
_WIN32_SHARE_READ_WRITE: Final[int] = 0x00000001 | 0x00000002
_WIN32_SHARE_ALL: Final[int] = _WIN32_SHARE_READ_WRITE | 0x00000004
_WIN32_OPEN_EXISTING: Final[int] = 3
_WIN32_FLAG_BACKUP_SEMANTICS: Final[int] = 0x02000000
_WIN32_FLAG_OPEN_REPARSE_POINT: Final[int] = 0x00200000
_WIN32_ATTR_DIRECTORY: Final[int] = 0x0010
_WIN32_ATTR_REPARSE_POINT: Final[int] = 0x0400
_WIN32_FILE_DISPOSITION_INFO: Final[int] = 4
_WIN32_ERR_FILE_NOT_FOUND: Final[int] = 2
_WIN32_ERR_PATH_NOT_FOUND: Final[int] = 3
_WIN32_PATH_BUF_LEN: Final[int] = 32768
_KERNEL32_CACHE: list[Any] = []


class _Win32FileTime(ctypes.Structure):
    """Win32 FILETIME layout for handle metadata inspection."""

    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


class _Win32HandleFileInfo(ctypes.Structure):
    """Win32 BY_HANDLE_FILE_INFORMATION layout for no-follow handle checks."""

    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTime", _Win32FileTime),
        ("ftLastAccessTime", _Win32FileTime),
        ("ftLastWriteTime", _Win32FileTime),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


class _Win32DispositionInfo(ctypes.Structure):
    """Win32 FILE_DISPOSITION_INFO layout for deleting an open file handle."""

    _fields_ = [("DeleteFile", ctypes.c_ubyte)]


def default_command_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute a fixed command array safely using subprocess.run with shell=False."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        errors="replace",
        timeout=DEFAULT_COMMAND_TIMEOUT_SECONDS,
        shell=False,
    )


def clean_str(val: Any, max_len: int = MAX_METADATA_STR_LEN) -> str | None:
    """Sanitize untrusted metadata string, removing escape sequences and control characters."""
    if val is None:
        return None
    raw = str(val)
    no_ansi = ANSI_ESCAPE_RE.sub("", raw)
    filtered = "".join(ch for ch in no_ansi if ch.isprintable() and ch != "\x00")
    stripped = filtered.strip()
    if not stripped:
        return None
    return stripped[:max_len]


def clean_int(val: Any, default: int = 0) -> int:
    """Convert value to non-negative int, returning default on failure."""
    if val is None:
        return default
    try:
        parsed = int(val)
        return max(0, parsed)
    except (ValueError, TypeError):
        return default


def sanitize_mount_point(val: Any) -> str | None:
    """Sanitize untrusted mountpoint path, preventing traversal and control characters."""
    cleaned = clean_str(val)
    if not cleaned or ".." in cleaned or not cleaned.startswith("/"):
        return None
    return cleaned


def is_symlink_or_reparse_point(path: Path) -> bool:
    """Return True when path is a symlink, Windows junction, or reparse point."""
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


def reject_symlink_target(parent_dir: Path, file_name: str, *, dir_fd: int | None = None) -> None:
    """Raise ValueError if the target entry inside parent_dir is a symlink."""
    if dir_fd is not None:
        try:
            entry_stat = os.lstat(file_name, dir_fd=dir_fd)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(entry_stat.st_mode):
            raise ValueError(f"Refusing symlink target: {file_name}")
        return
    if is_symlink_or_reparse_point(parent_dir / file_name):
        raise ValueError(f"Refusing symlink target: {file_name}")


def _win32_kernel32() -> Any:
    """Return configured kernel32 library handle functions for Win32 confinement."""
    if _KERNEL32_CACHE:
        return _KERNEL32_CACHE[0]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.restype = ctypes.c_int
    k32.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.GetFileInformationByHandle.restype = ctypes.c_int
    k32.GetFinalPathNameByHandleW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    k32.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
    k32.SetFileInformationByHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    k32.SetFileInformationByHandle.restype = ctypes.c_int
    _KERNEL32_CACHE.append(k32)
    return k32


def close_win32_handle(handle: int) -> None:
    """Close an open Win32 kernel handle."""
    if sys.platform == "win32":
        _win32_kernel32().CloseHandle(ctypes.c_void_p(handle))


def _normalize_win32_final_path(raw_path: str) -> str:
    """Strip Win32 extended-length path prefixes for canonical comparison."""
    if raw_path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + raw_path[8:]
    if raw_path.startswith("\\\\?\\"):
        return raw_path[4:]
    return raw_path


def _open_win32_handle(path: Path, access: int, share: int, flags: int) -> int | None:
    """Open path on Win32 without following reparse points, returning None when absent."""
    k32 = _win32_kernel32()
    handle = k32.CreateFileW(
        str(path),
        access,
        share,
        None,
        _WIN32_OPEN_EXISTING,
        flags | _WIN32_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle is None or handle == invalid:
        err = ctypes.get_last_error()
        if err in (_WIN32_ERR_FILE_NOT_FOUND, _WIN32_ERR_PATH_NOT_FOUND):
            return None
        raise OSError(err, os.strerror(err), str(path))
    return int(handle)


def _validate_win32_handle(
    handle: int, expected: Path, *, expect_dir: bool, check_parent: bool
) -> None:
    """Verify open Win32 handle is not a reparse point and resolves inside expected."""
    k32 = _win32_kernel32()
    info = _Win32HandleFileInfo()
    if not k32.GetFileInformationByHandle(ctypes.c_void_p(handle), ctypes.byref(info)):
        err = ctypes.get_last_error()
        raise OSError(err, os.strerror(err))
    if info.dwFileAttributes & _WIN32_ATTR_REPARSE_POINT:
        raise ValueError("USB metadata path cannot be a symbolic link.")
    if bool(info.dwFileAttributes & _WIN32_ATTR_DIRECTORY) != expect_dir:
        raise ValueError("USB metadata entry has unexpected filesystem type.")
    buf = ctypes.create_unicode_buffer(_WIN32_PATH_BUF_LEN)
    length = k32.GetFinalPathNameByHandleW(ctypes.c_void_p(handle), buf, _WIN32_PATH_BUF_LEN, 0)
    if length == 0 or length >= _WIN32_PATH_BUF_LEN:
        err = ctypes.get_last_error()
        raise OSError(err, os.strerror(err))
    resolved = _normalize_win32_final_path(buf.value)
    actual_ref = os.path.normcase(os.path.dirname(resolved) if check_parent else resolved)
    expected_ref = os.path.normcase(str(expected.resolve(strict=True)))
    if actual_ref != expected_ref:
        raise ValueError("USB metadata path resolves outside the mount root.")


def open_win32_locked_dir(path: Path, expected_dir: Path) -> int | None:
    """Open and share-delete-lock a real Win32 directory without following reparse points."""
    handle = _open_win32_handle(
        path,
        _WIN32_GENERIC_READ,
        _WIN32_SHARE_READ_WRITE,
        _WIN32_FLAG_BACKUP_SEMANTICS,
    )
    if handle is None:
        return None
    try:
        _validate_win32_handle(handle, expected_dir, expect_dir=True, check_parent=False)
    except BaseException:
        close_win32_handle(handle)
        raise
    return handle


def open_win32_confined_read_fd(target: Path, expected_parent: Path) -> int | None:
    """Open a confined regular file handle on Win32 without following reparse points."""
    handle = _open_win32_handle(target, _WIN32_GENERIC_READ, _WIN32_SHARE_ALL, 0)
    if handle is None:
        return None
    try:
        _validate_win32_handle(handle, expected_parent, expect_dir=False, check_parent=True)
        msvcrt_mod = importlib.import_module("msvcrt")
        return int(msvcrt_mod.open_osfhandle(handle, os.O_RDONLY))
    except BaseException:
        close_win32_handle(handle)
        raise


def unlink_win32_confined_file(target: Path, expected_parent: Path) -> bool:
    """Delete a confined regular file handle on Win32 without following reparse points."""
    access = _WIN32_DELETE | _WIN32_FILE_READ_ATTRIBUTES
    handle = _open_win32_handle(target, access, _WIN32_SHARE_ALL, 0)
    if handle is None:
        return False
    k32 = _win32_kernel32()
    try:
        _validate_win32_handle(handle, expected_parent, expect_dir=False, check_parent=True)
        disp = _Win32DispositionInfo(1)
        ok = k32.SetFileInformationByHandle(
            ctypes.c_void_p(handle),
            _WIN32_FILE_DISPOSITION_INFO,
            ctypes.byref(disp),
            ctypes.sizeof(disp),
        )
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(err, os.strerror(err), str(target))
    finally:
        close_win32_handle(handle)
    return True


def verify_fd_identity_and_parent(descriptor: int, target: Path, expected_parent: Path) -> None:
    """Verify an open file descriptor matches lstat identity and canonical parent dir."""
    fd_stat = os.fstat(descriptor)
    if not stat.S_ISREG(fd_stat.st_mode):
        raise ValueError("USB metadata target must be a regular file.")
    link_stat = os.lstat(target)
    if stat.S_ISLNK(link_stat.st_mode) or getattr(link_stat, "st_reparse_tag", 0):
        raise ValueError("USB metadata target cannot be a symbolic link.")
    if (fd_stat.st_dev, fd_stat.st_ino) != (link_stat.st_dev, link_stat.st_ino):
        raise ValueError("USB metadata target changed during open.")
    if target.resolve(strict=True).parent != expected_parent.resolve(strict=True):
        raise ValueError("USB metadata path resolves outside the mount root.")


def read_bounded_fd_utf8(descriptor: int, *, max_bytes: int = MAX_USB_JSON_BYTES) -> str | None:
    """Read UTF-8 text from an open descriptor within max_bytes, returning None for non-files."""
    try:
        fd_stat = os.fstat(descriptor)
        if not stat.S_ISREG(fd_stat.st_mode):
            os.close(descriptor)
            return None
        if fd_stat.st_size > max_bytes:
            os.close(descriptor)
            raise ValueError("USB JSON file exceeds maximum allowed size.")
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        raise
    with os.fdopen(descriptor, "rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("USB JSON file exceeds maximum allowed size.")
    return raw.decode("utf-8")


def read_bounded_json_file(file_path: Path, *, max_bytes: int = MAX_USB_JSON_BYTES) -> str:
    """Read a regular local JSON file safely without following symlinks and within max_bytes."""
    if is_symlink_or_reparse_point(file_path):
        raise ValueError(f"Refusing symlink JSON file: {file_path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(file_path, flags)
    try:
        verify_fd_identity_and_parent(descriptor, file_path, file_path.parent)
    except BaseException:
        os.close(descriptor)
        raise
    content = read_bounded_fd_utf8(descriptor, max_bytes=max_bytes)
    if content is None:
        raise ValueError(f"Expected regular JSON file: {file_path}")
    return content


def _write_temp_json_via_fd(dir_fd: int, file_name: str, text: str) -> str:
    """Create and flush a temporary JSON file exclusively inside dir_fd."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    for _ in range(MAX_TEMP_ATTEMPTS):
        temp_name = f".{file_name}.{secrets.token_hex(8)}.tmp"
        try:
            descriptor = os.open(temp_name, flags, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp_name, dir_fd=dir_fd)
            raise
        return temp_name
    raise OSError("Unable to allocate unique temporary JSON file.")


def _write_temp_json_via_path(parent_dir: Path, file_name: str, text: str) -> Path:
    """Create and flush a temporary JSON file inside parent_dir."""
    descriptor, temp_name = tempfile.mkstemp(
        dir=parent_dir, prefix=f".{file_name}.", suffix=".tmp", text=True
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def write_json_atomic(
    parent_dir: Path,
    file_name: str,
    payload: Mapping[str, object],
    *,
    dir_fd: int | None = None,
    create_parent: bool = True,
    reject_target_symlink: bool = False,
) -> Path:
    """Atomically write UTF-8 JSON to file_name inside parent_dir or dir_fd."""
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    target = parent_dir / file_name
    if dir_fd is not None:
        return _write_json_atomic_dir_fd(parent_dir, file_name, text, dir_fd, reject_target_symlink)
    if create_parent:
        parent_dir.mkdir(parents=True, exist_ok=True)
    if is_symlink_or_reparse_point(parent_dir):
        raise ValueError(f"Metadata folder cannot be a symbolic link: {parent_dir}")
    if reject_target_symlink:
        reject_symlink_target(parent_dir, file_name)
    temp_path = _write_temp_json_via_path(parent_dir, file_name, text)
    try:
        if is_symlink_or_reparse_point(parent_dir):
            raise ValueError(f"Metadata folder cannot be a symbolic link: {parent_dir}")
        if reject_target_symlink:
            reject_symlink_target(parent_dir, file_name)
        os.replace(temp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise
    return target


def _write_json_atomic_dir_fd(
    parent_dir: Path,
    file_name: str,
    text: str,
    dir_fd: int,
    reject_target_symlink: bool,
) -> Path:
    """Atomically write UTF-8 JSON through an open dir_fd without following symlinks."""
    if reject_target_symlink:
        reject_symlink_target(parent_dir, file_name, dir_fd=dir_fd)
    temp_name = _write_temp_json_via_fd(dir_fd, file_name, text)
    try:
        if reject_target_symlink:
            reject_symlink_target(parent_dir, file_name, dir_fd=dir_fd)
        os.replace(temp_name, file_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name, dir_fd=dir_fd)
        raise
    return parent_dir / file_name
