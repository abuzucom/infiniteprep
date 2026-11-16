"""Shared helpers for USB drive inspectors (USB-1 #153, USB-2 #154).

Untrusted metadata sanitization and safe subprocess execution shared by the
platform inspectors. Every helper treats external command output as untrusted
input and never executes reconstructed text.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

DEFAULT_COMMAND_TIMEOUT_SECONDS: Final[float] = 15.0
MAX_METADATA_STR_LEN: Final[int] = 512
MAX_TEMP_ATTEMPTS: Final[int] = 16
ANSI_ESCAPE_RE: Final[re.Pattern[str]] = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


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


def reject_symlink_target(
    parent_dir: Path, file_name: str, *, dir_fd: int | None = None
) -> None:
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
        return target
    if create_parent:
        parent_dir.mkdir(parents=True, exist_ok=True)
    if reject_target_symlink:
        reject_symlink_target(parent_dir, file_name)
    temp_path = _write_temp_json_via_path(parent_dir, file_name, text)
    try:
        if reject_target_symlink:
            reject_symlink_target(parent_dir, file_name)
        os.replace(temp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise
    return target
