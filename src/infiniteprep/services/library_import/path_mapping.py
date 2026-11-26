"""Path styles, prefix mappings, and root containment (D333).

A DJ library names its audio files by path. A source library is untrusted
input, so the framework reads no file outside a root the DJ chose. Every
mapped path and every uncovered path must resolve inside that root, and no
symbolic link may carry a path out of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import unquote

from infiniteprep.services.library_import.filesystem import (
    resolve_inside_root,
)

__all__ = [
    "PathMapping",
    "PathMappingError",
    "PathStyle",
    "apply_mapping",
    "comparison_path",
    "detect_path_style",
    "normalize_path",
    "resolve_inside_root",
    "reverse_mapping",
]

WINDOWS_DRIVE_SEPARATOR: Final[str] = ":"
TRAKTOR_VOLUME_PREFIX: Final[str] = "/:"
FILE_URL_SCHEME: Final[str] = "file://"
PARENT_SEGMENT: Final[str] = ".."
# A file URL that names another file URL is decoded this many times at most.
MAX_FILE_URL_DEPTH: Final[int] = 4


class PathStyle(StrEnum):
    """The four path styles a DJ library can use."""

    WINDOWS = "windows"
    POSIX = "posix"
    FILE_URL = "file_url"
    TRAKTOR_VOLUME = "traktor_volume"


class PathMappingError(ValueError):
    """Raised when a mapping would place a path outside the chosen root."""


@dataclass(frozen=True)
class PathMapping:
    """A prefix mapping between a source library and the current machine.

    source_prefix is the prefix as the source library wrote it. target_prefix
    is the prefix on the current machine.
    """

    source_prefix: str
    target_prefix: str


def detect_path_style(path_text: str) -> PathStyle:
    """Return the style of one path."""
    lowered = path_text.casefold()
    if lowered.startswith(FILE_URL_SCHEME):
        return PathStyle.FILE_URL
    if path_text.startswith(TRAKTOR_VOLUME_PREFIX):
        return PathStyle.TRAKTOR_VOLUME
    if _has_windows_drive(path_text):
        return PathStyle.WINDOWS
    if path_text.startswith("\\") or path_text.startswith("/"):
        return PathStyle.POSIX
    return PathStyle.POSIX


def _has_windows_drive(path_text: str) -> bool:
    """Return True when the text opens with a drive letter and a separator."""
    if len(path_text) < 3:
        return False
    drive = path_text[0]
    separator = path_text[1]
    return (
        drive.isascii()
        and drive.isalpha()
        and separator == WINDOWS_DRIVE_SEPARATOR
        and path_text[2] in ("\\", "/")
    )


def normalize_path(path_text: str) -> str:
    """Return the path in its own style, cleaned for display."""
    style = detect_path_style(path_text)
    if style is PathStyle.FILE_URL:
        return _normalize_file_url(path_text)
    if style is PathStyle.TRAKTOR_VOLUME:
        return _normalize_traktor_volume(path_text)
    if style is PathStyle.WINDOWS:
        return _normalize_windows(path_text)
    return _normalize_posix(path_text)


def _file_url_body(path_text: str) -> str:
    """Return the decoded body of a file URL.

    A file URL that names another file URL is decoded until it names a plain
    path. A value that keeps nesting past the depth limit is refused.
    """
    body = path_text
    for _depth in range(MAX_FILE_URL_DEPTH):
        if not body.casefold().startswith(FILE_URL_SCHEME):
            return body
        body = unquote(body[len(FILE_URL_SCHEME) :])
    if body.casefold().startswith(FILE_URL_SCHEME):
        raise PathMappingError("The file URL nests too deeply.")
    return body


def _normalize_file_url(path_text: str) -> str:
    """Return the file URL with decoded text, collapsed slashes, and fixed drive case."""
    decoded = _file_url_body(path_text)
    if _has_windows_drive(decoded.lstrip("/")):
        return FILE_URL_SCHEME + "/" + _normalize_windows(decoded.lstrip("/"))
    return FILE_URL_SCHEME + _normalize_posix(decoded)


def _normalize_traktor_volume(path_text: str) -> str:
    """Return the Traktor volume path with an upper-case volume and forward slashes."""
    body = path_text[len(TRAKTOR_VOLUME_PREFIX) :]
    volume, separator, rest = body.partition("/")
    if not separator:
        return TRAKTOR_VOLUME_PREFIX + volume.upper()
    return TRAKTOR_VOLUME_PREFIX + volume.upper() + "/" + _collapse_slashes(rest.replace("\\", "/"))


def _normalize_windows(path_text: str) -> str:
    """Return the Windows path with an upper-case drive and forward slashes."""
    drive = path_text[:2].upper()
    rest = path_text[2:].replace("\\", "/")
    return drive + _collapse_slashes(rest)


def _normalize_posix(path_text: str) -> str:
    """Return the path with forward slashes and no repeated separator."""
    return _collapse_slashes(path_text.replace("\\", "/"))


def _collapse_slashes(text: str) -> str:
    """Return the text with repeated slashes collapsed and no trailing slash."""
    collapsed = text
    while "//" in collapsed:
        collapsed = collapsed.replace("//", "/")
    if len(collapsed) > 1 and collapsed.endswith("/"):
        collapsed = collapsed[:-1]
    return collapsed


def comparison_path(path_text: str) -> str:
    """Return the form used to compare two paths of the same style.

    A Windows path and a Traktor volume path ignore case. A POSIX path keeps
    its case, because a Linux file system keeps it too.
    """
    style = detect_path_style(path_text)
    if style is PathStyle.FILE_URL:
        return FILE_URL_SCHEME + comparison_path(_file_url_body(path_text))
    if style is PathStyle.TRAKTOR_VOLUME:
        body = path_text[len(TRAKTOR_VOLUME_PREFIX) :]
        volume, separator, rest = body.partition("/")
        if not separator:
            return TRAKTOR_VOLUME_PREFIX + volume.upper()
        return TRAKTOR_VOLUME_PREFIX + volume.upper() + "/" + rest.casefold()
    if style is PathStyle.WINDOWS:
        return _normalize_windows(path_text).casefold()
    return _normalize_posix(path_text)


def path_starts_with(path_text: str, prefix_text: str) -> bool:
    """Return True when the path sits at or below the prefix."""
    path_value = comparison_path(path_text)
    prefix_value = comparison_path(prefix_text)
    if path_value == prefix_value:
        return True
    if not prefix_value.endswith("/"):
        prefix_value += "/"
    return path_value.startswith(prefix_value)


def apply_mapping(path_text: str, mapping: PathMapping) -> str:
    """Map a source path onto the target prefix of the mapping.

    A path that the mapping does not cover keeps its own text. The caller
    checks the result against the root the DJ chose.
    """
    if not path_starts_with(path_text, mapping.source_prefix):
        return normalize_path(path_text)
    remainder = _mapping_remainder(path_text, mapping.source_prefix)
    if _has_parent_segment(remainder):
        raise PathMappingError(
            f"The mapping for {mapping.source_prefix} would move a path above the root."
        )
    if _has_drive_segment(remainder):
        raise PathMappingError(
            f"The mapping for {mapping.source_prefix} would name a drive of its own."
        )
    target = normalize_path(mapping.target_prefix)
    return _join_prefix(target, remainder)


def reverse_mapping(path_text: str, mapping: PathMapping) -> str:
    """Map a current-machine path back onto the source prefix of the mapping.

    A path that climbs above the target prefix is refused. The reverse mapping
    names a path in the source layout, so a path that leaves the target prefix
    would name a place the source library never described.
    """
    if not path_starts_with(path_text, mapping.target_prefix):
        return normalize_path(path_text)
    remainder = _mapping_remainder(path_text, mapping.target_prefix)
    if _has_parent_segment(remainder):
        raise PathMappingError(
            f"The mapping for {mapping.target_prefix} would move a path above the source prefix."
        )
    if _has_drive_segment(remainder):
        raise PathMappingError(
            f"The mapping for {mapping.target_prefix} would name a drive of its own."
        )
    source = normalize_path(mapping.source_prefix)
    return _join_prefix(source, remainder)


def _mapping_remainder(path_text: str, prefix_text: str) -> str:
    """Return the part of the path below the prefix using canonical path segments."""
    normalized = normalize_path(path_text)
    prefix = normalize_path(prefix_text)
    if prefix == "/":
        return normalized.lstrip("/")
    path_parts = normalized.split("/")
    prefix_parts = prefix.rstrip("/").split("/")
    return "/".join(path_parts[len(prefix_parts) :])


def _has_parent_segment(remainder: str) -> bool:
    """Return True when the remainder climbs above its own prefix."""
    return any(segment == PARENT_SEGMENT for segment in remainder.split("/"))


def _has_drive_segment(remainder: str) -> bool:
    """Return True when the remainder names a drive of its own.

    A remainder such as ``C:`` would make the joined path ambiguous, because
    the path would name two drives at once.
    """
    return any(_has_windows_drive(segment + "/") for segment in remainder.split("/"))


def _join_prefix(prefix: str, remainder: str) -> str:
    """Return the prefix and the remainder as one path."""
    if not remainder:
        return prefix
    if prefix.endswith("/"):
        return prefix + remainder
    return prefix + "/" + remainder
