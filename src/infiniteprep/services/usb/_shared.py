"""Shared helpers for USB drive inspectors (USB-1 #153, USB-2 #154).

Untrusted metadata sanitization and safe subprocess execution shared by the
platform inspectors. Every helper treats external command output as untrusted
input and never executes reconstructed text.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import importlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Final

from infiniteprep.services.usb.models import SpaceInfo

DEFAULT_COMMAND_TIMEOUT_SECONDS: Final[float] = 15.0
MAX_METADATA_STR_LEN: Final[int] = 512
# PATH_MAX on Linux. No mounted volume has a longer path.
MAX_MOUNT_PATH_LEN: Final[int] = 4096
FIRST_PRINTABLE_CODE_POINT: Final[int] = 0x20
DEL_CODE_POINT: Final[int] = 0x7F
PARENT_DIR_PART: Final[str] = ".."
# Path components that add no folder: an empty part between separators and ".".
CURRENT_DIR_PARTS: Final[frozenset[str]] = frozenset({"", "."})
# Windows opens a device for these names in any folder, with or without an extension.
WINDOWS_RESERVED_DEVICE_NAMES: Final[frozenset[str]] = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{number}" for number in range(1, 10)]
    + [f"LPT{number}" for number in range(1, 10)]
)
# A colon inside a Windows name selects a drive or an alternate data stream.
WINDOWS_STREAM_SEPARATOR: Final[str] = ":"
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
WIN32_NO_ERROR_MESSAGE: Final[str] = "The Win32 call failed without an error code."
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
    # JSON and plist both accept an infinite number, and int() of it overflows.
    except (ValueError, TypeError, OverflowError):
        return default


def deduplicate_space_entries(
    space_entries: list[tuple[str, SpaceInfo]],
) -> tuple[tuple[str, SpaceInfo], ...]:
    """Keep the first space entry of each mount point, in the original order.

    The Linux, macOS, and Windows inspectors each held a copy of this loop.
    """
    unique: dict[str, SpaceInfo] = {}
    for mount, space in space_entries:
        unique.setdefault(mount, space)
    return tuple(unique.items())


def sanitize_mount_point(val: Any) -> str | None:
    """Return an untrusted POSIX mount path unchanged when it is safe, or None.

    A cleaned copy of an unsafe path can name another folder or another volume. The probe
    output therefore keeps its exact text or yields no mount point at all.
    """
    if not isinstance(val, str) or not val.startswith("/") or len(val) > MAX_MOUNT_PATH_LEN:
        return None
    if PARENT_DIR_PART in val.split("/") or not val.isprintable():
        return None
    return val


def has_path_control_characters(text: str) -> bool:
    """Return True when text holds a C0 control character or DEL."""
    return any(
        ord(char) < FIRST_PRINTABLE_CODE_POINT or ord(char) == DEL_CODE_POINT for char in text
    )


def _path_parts(text: str) -> list[str]:
    """Split text on both the POSIX and the Windows separator."""
    return text.replace("\\", "/").split("/")


def _has_unsafe_path_text(text: str) -> bool:
    """Return True for empty text, control characters, or a parent directory segment."""
    if not text or has_path_control_characters(text):
        return True
    return PARENT_DIR_PART in _path_parts(text)


def _is_windows_special_name(part: str) -> bool:
    """Return True for a device name or a name that holds a stream or drive colon."""
    if WINDOWS_STREAM_SEPARATOR in part:
        return True
    # Windows ignores the extension and trailing spaces when it matches a device name.
    stem = part.split(".", 1)[0].rstrip(" ").upper()
    return stem in WINDOWS_RESERVED_DEVICE_NAMES


def is_safe_relative_path(text: str) -> bool:
    """Return True when text names a file path below its root on POSIX and on Windows."""
    if _has_unsafe_path_text(text):
        return False
    parts = _path_parts(text)
    if any(_is_windows_special_name(part) for part in parts):
        return False
    # "." and "./" name the root itself, not a path below it.
    if all(part in CURRENT_DIR_PARTS for part in parts):
        return False
    windows_path = PureWindowsPath(text)
    return not (text.startswith(("/", "\\")) or windows_path.drive or windows_path.root)


def is_safe_absolute_path(text: str) -> bool:
    """Return True when text is an absolute POSIX or Windows path without traversal."""
    if _has_unsafe_path_text(text):
        return False
    return PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute()


def load_json_text(text: str, label: str) -> object:
    """Parse JSON text and report nesting past the recursion limit as malformed.

    The callers catch ValueError for a bad file. A deeply nested file raises
    RecursionError in the parser instead.
    """
    try:
        return json.loads(text)
    except RecursionError as exc:
        raise ValueError(f"{label} is malformed: its JSON nesting is too deep.") from exc


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


def win32_error(code: int, path: Path | None = None) -> OSError:
    """Return an OSError for a Win32 error code with its Windows message.

    os.strerror reads C errno values. A Win32 code such as 32 (sharing violation) then
    named the wrong error, here "Broken pipe". The winerror argument lets Python pick
    the matching subclass and errno. Code 0 means the call set no error.
    """
    if code == 0:
        return OSError(None, WIN32_NO_ERROR_MESSAGE, None if path is None else str(path))
    message = ctypes.FormatError(code)
    return OSError(None, message, None if path is None else str(path), code)


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
        raise win32_error(err, path)
    return int(handle)


def _validate_win32_handle(
    handle: int, expected: Path, *, expect_dir: bool, check_parent: bool
) -> None:
    """Verify open Win32 handle is not a reparse point and resolves inside expected."""
    k32 = _win32_kernel32()
    info = _Win32HandleFileInfo()
    if not k32.GetFileInformationByHandle(ctypes.c_void_p(handle), ctypes.byref(info)):
        raise win32_error(ctypes.get_last_error())
    if info.dwFileAttributes & _WIN32_ATTR_REPARSE_POINT:
        raise ValueError("USB metadata path cannot be a symbolic link.")
    if bool(info.dwFileAttributes & _WIN32_ATTR_DIRECTORY) != expect_dir:
        raise ValueError("USB metadata entry has unexpected filesystem type.")
    buf = ctypes.create_unicode_buffer(_WIN32_PATH_BUF_LEN)
    length = k32.GetFinalPathNameByHandleW(ctypes.c_void_p(handle), buf, _WIN32_PATH_BUF_LEN, 0)
    if length == 0:
        raise win32_error(ctypes.get_last_error())
    if length >= _WIN32_PATH_BUF_LEN:
        raise OSError(None, "The final path of the USB metadata entry exceeds the path buffer.")
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
            raise win32_error(ctypes.get_last_error(), target)
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
    """Read UTF-8 text from an open regular-file descriptor within max_bytes."""
    try:
        fd_stat = os.fstat(descriptor)
        if not stat.S_ISREG(fd_stat.st_mode):
            raise ValueError("USB JSON target must be a regular file.")
        if fd_stat.st_size > max_bytes:
            raise ValueError("USB JSON file exceeds maximum allowed size.")
    # The handler closes the descriptor once. A second close could hit a descriptor
    # number another thread received in between.
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
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
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


def supports_dir_fd_nofollow() -> bool:
    """Return True when the platform supports dir_fd atomic operations with O_NOFOLLOW."""
    return (
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
        and os.replace in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
        and os.lstat in os.supports_dir_fd
    )


def parse_utc_datetime(raw_text: str, field_name: str = "timestamp") -> datetime:
    """Parse a timezone-aware ISO-8601 timestamp string into a UTC datetime."""
    parsed = datetime.fromisoformat(raw_text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Timestamp '{field_name}' must include a timezone offset.")
    try:
        return parsed.astimezone(UTC)
    except OverflowError as exc:
        # A valid offset can move a date at year 1 or 9999 past the datetime range.
        raise ValueError(f"Timestamp '{field_name}' falls outside the supported range.") from exc


def normalize_iso_utc(raw_text: str, field_name: str = "timestamp") -> str:
    """Validate and normalize an ISO-8601 timestamp with offset into canonical UTC Z text."""
    return parse_utc_datetime(raw_text, field_name).isoformat().replace("+00:00", "Z")


def _ensure_confined_root(allowed_root: Path, *, create: bool) -> Path:
    """Verify allowed_root is not a symlink and return its resolved directory path."""
    if is_symlink_or_reparse_point(allowed_root):
        raise ValueError(f"Storage root cannot be a symbolic link: {allowed_root}")
    if create:
        allowed_root.mkdir(parents=True, exist_ok=True)
    elif not allowed_root.exists():
        raise FileNotFoundError(allowed_root)
    if is_symlink_or_reparse_point(allowed_root):
        raise ValueError(f"Storage root cannot be a symbolic link: {allowed_root}")
    resolved_root = allowed_root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError(f"Storage root is not a directory: {allowed_root}")
    return resolved_root


def validate_confined_ancestors(target_dir: Path, allowed_root: Path, *, create: bool) -> Path:
    """Walk components from allowed_root to target_dir, rejecting symlinks and escapes."""
    resolved_root = _ensure_confined_root(allowed_root, create=create)
    try:
        relative = target_dir.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("Target directory must stay inside the allowed storage root.") from exc
    current = allowed_root
    for part in relative.parts:
        current = current / part
        if is_symlink_or_reparse_point(current):
            raise ValueError(f"Path component cannot be a symbolic link: {current}")
        if create and not current.exists():
            current.mkdir(exist_ok=True)
        elif not current.exists():
            raise FileNotFoundError(current)
    resolved_target = target_dir.resolve(strict=True)
    if not resolved_target.is_relative_to(resolved_root):
        raise ValueError("Target directory resolves outside the allowed storage root.")
    return resolved_target


def _open_posix_dir_step(current_fd: int, part: str, *, create: bool) -> int | None:
    """Open one child directory relative to current_fd without following symlinks."""
    if create:
        with contextlib.suppress(FileExistsError):
            os.mkdir(part, 0o755, dir_fd=current_fd)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(part, flags, dir_fd=current_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError(f"Path component cannot be a symbolic link: {part}") from exc
        raise


def _open_posix_confined_dir(
    target_dir: Path, allowed_root: Path, *, create: bool
) -> tuple[Path, int] | None:
    """Walk components with dir_fd + O_NOFOLLOW from allowed_root to target_dir."""
    if is_symlink_or_reparse_point(allowed_root):
        raise ValueError(f"Storage root cannot be a symbolic link: {allowed_root}")
    if create:
        allowed_root.mkdir(parents=True, exist_ok=True)
    elif not allowed_root.exists():
        return None
    try:
        relative = target_dir.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("Target directory must stay inside the allowed storage root.") from exc
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        current_fd = os.open(allowed_root, flags)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError(f"Storage root cannot be a symbolic link: {allowed_root}") from exc
        raise
    for part in relative.parts:
        try:
            next_fd = _open_posix_dir_step(current_fd, part, create=create)
        finally:
            os.close(current_fd)
        if next_fd is None:
            return None
        current_fd = next_fd
    return target_dir, current_fd


@contextlib.contextmanager
def open_confined_directory_context(
    target_dir: Path, allowed_root: Path, *, create: bool
) -> Iterator[tuple[Path, int | None] | None]:
    """Yield (resolved_dir, optional_dir_fd) confined strictly under allowed_root."""
    if supports_dir_fd_nofollow():
        opened = _open_posix_confined_dir(target_dir, allowed_root, create=create)
        if opened is None:
            yield None
            return
        confined_dir, dir_fd = opened
        try:
            yield confined_dir, dir_fd
        finally:
            os.close(dir_fd)
        return
    try:
        resolved_dir = validate_confined_ancestors(target_dir, allowed_root, create=create)
    except FileNotFoundError:
        yield None
        return
    if sys.platform == "win32":
        handle = open_win32_locked_dir(resolved_dir, resolved_dir)
        if handle is None:
            yield None
            return
        try:
            yield resolved_dir, None
        finally:
            close_win32_handle(handle)
        return
    yield resolved_dir, None


def read_confined_dir_json_text(
    resolved_dir: Path, file_name: str, *, dir_fd: int | None, max_bytes: int = MAX_USB_JSON_BYTES
) -> str | None:
    """Read UTF-8 JSON text from file_name inside a confined directory handle or dir_fd."""
    nonblock = getattr(os, "O_NONBLOCK", 0)
    if dir_fd is not None:
        flags = os.O_RDONLY | os.O_NOFOLLOW | nonblock
        try:
            descriptor = os.open(file_name, flags, dir_fd=dir_fd)
        except FileNotFoundError:
            return None
        return read_bounded_fd_utf8(descriptor, max_bytes=max_bytes)
    if sys.platform == "win32":
        descriptor = open_win32_confined_read_fd(resolved_dir / file_name, resolved_dir)
        return None if descriptor is None else read_bounded_fd_utf8(descriptor, max_bytes=max_bytes)
    target = resolved_dir / file_name
    reject_symlink_target(resolved_dir, file_name)
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | nonblock)
    except FileNotFoundError:
        return None
    try:
        verify_fd_identity_and_parent(descriptor, target, resolved_dir)
    except BaseException:
        os.close(descriptor)
        raise
    return read_bounded_fd_utf8(descriptor, max_bytes=max_bytes)


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
