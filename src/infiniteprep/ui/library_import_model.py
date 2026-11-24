"""Texts and values for the library import dialog.

Every path, version, and format name comes from an installed library or from a
vendor page. Each one is untrusted, so every one renders as plain text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from infiniteprep.services.library_import.locations import format_display_name
from infiniteprep.services.library_import.locator import LocatedLibrary

WINDOW_TITLE: Final[str] = "Import DJ Library"
STEP_FIND_TITLE: Final[str] = "Find library"
STEP_MAP_TITLE: Final[str] = "Map paths"
STEP_REVIEW_TITLE: Final[str] = "Review and commit"
FORMAT_LABEL: Final[str] = "Format"
VERSION_LABEL: Final[str] = "Version"
MODIFIED_LABEL: Final[str] = "Last changed"
PATH_LABEL: Final[str] = "Path"
OBSERVED_SUFFIX: Final[str] = " (observed)"
VERSION_UNKNOWN_TEXT: Final[str] = "Unknown"
EMPTY_LIST_TEXT: Final[str] = "No DJ library was found."
EMPTY_CONFLICT_TEXT: Final[str] = "No value conflicts with the library."
CHOOSE_FILE_BUTTON: Final[str] = "Choose file..."
NEXT_BUTTON: Final[str] = "Next"
BACK_BUTTON: Final[str] = "Back"
COMMIT_BUTTON: Final[str] = "Import"
CANCEL_BUTTON: Final[str] = "Cancel"
SOURCE_PREFIX_LABEL: Final[str] = "Library path"
TARGET_PREFIX_LABEL: Final[str] = "This computer"
ROOT_LABEL: Final[str] = "Keep inside"
ROOT_PLACEHOLDER: Final[str] = "The folder your music lives in"
ADD_MAPPING_BUTTON: Final[str] = "Add mapping"
REMOVE_MAPPING_BUTTON: Final[str] = "Remove"
CONFLICT_LABEL: Final[str] = "Conflicts"
PENDING_LABEL: Final[str] = "Values waiting for storage"
UNRESOLVED_LABEL: Final[str] = "Files not found"
CONFLICT_VALUE_FORMAT: Final[str] = (
    "{field_name}: library has {imported_value}, app has {stored_value}"
)
PENDING_VALUE_FORMAT: Final[str] = "{field_name}"
TIMESTAMP_FORMAT: Final[str] = "%Y-%m-%d %H:%M"


@dataclass(frozen=True)
class LocatedLibraryRow:
    """One located library, ready for the list."""

    label: str
    format_text: str
    version_text: str
    modified_text: str
    path_text: str


def format_timestamp(timestamp: float | None) -> str:
    """Return the last-modified time as plain text in the local time zone."""
    if timestamp is None:
        return VERSION_UNKNOWN_TEXT
    moment = datetime.fromtimestamp(timestamp, tz=UTC).astimezone()
    return moment.strftime(TIMESTAMP_FORMAT)


def format_version(version: str | None) -> str:
    """Return the version as plain text, or a fixed line when it is unknown."""
    return version if version else VERSION_UNKNOWN_TEXT


def build_row(library: LocatedLibrary) -> LocatedLibraryRow:
    """Return the row of one located library."""
    version_text = format_version(library.version)
    if library.observed:
        version_text += OBSERVED_SUFFIX
    return LocatedLibraryRow(
        label=library.file_path.name,
        format_text=format_display_name(library.format_id),
        version_text=version_text,
        modified_text=format_timestamp(library.modified_at),
        path_text=str(library.file_path),
    )


def build_rows(libraries: Sequence[LocatedLibrary]) -> list[LocatedLibraryRow]:
    """Return the rows of every located library."""
    return [build_row(library) for library in libraries]


def format_conflict(field_name: str, imported_value: str, stored_value: str) -> str:
    """Return the line of one conflict."""
    return CONFLICT_VALUE_FORMAT.format(
        field_name=field_name,
        imported_value=imported_value,
        stored_value=stored_value,
    )


def format_pending(field_name: str) -> str:
    """Return the line of one value that waits for its storage."""
    return PENDING_VALUE_FORMAT.format(field_name=field_name)


def format_count(count: int, singular: str, plural: str) -> str:
    """Return a count with the noun that fits it."""
    return f"{count} {singular if count == 1 else plural}"
