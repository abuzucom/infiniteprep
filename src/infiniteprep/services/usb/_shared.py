"""Shared helpers for USB drive inspectors (D161, D183, Rule 1).

Untrusted metadata sanitization and safe subprocess execution shared by the
platform inspectors. Every helper treats external command output as untrusted
input and never executes reconstructed text.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any, Final

DEFAULT_COMMAND_TIMEOUT_SECONDS: Final[float] = 15.0
MAX_METADATA_STR_LEN: Final[int] = 512
ANSI_ESCAPE_RE: Final[re.Pattern[str]] = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def default_command_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute a fixed command array safely using subprocess.run with shell=False."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
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
