"""Typed sort keys and row ordering for the library table (issue 248)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Final

from infiniteprep.config.settings import (
    UNSORTED_PLACEMENT_LAST,
)
from infiniteprep.domain.enums import (
    KeyDisplayNotation,
    KeyMode,
    PitchClass,
    ReviewStatus,
    format_canonical_key,
)

FILENAME_COLUMN: Final[int] = 0
FORMAT_COLUMN: Final[int] = 1
DURATION_COLUMN: Final[int] = 2
ARTIST_COLUMN: Final[int] = 3
TITLE_COLUMN: Final[int] = 4
MIX_COLUMN: Final[int] = 5
LABEL_COLUMN: Final[int] = 6
BPM_COLUMN: Final[int] = 7
KEY_COLUMN: Final[int] = 8
REVIEW_STATUS_COLUMN: Final[int] = 9
WARNINGS_COLUMN: Final[int] = 10
SORT_COLUMN_COUNT: Final[int] = 11

TEXT_SORT: Final[str] = "text"
DURATION_SORT: Final[str] = "duration"
BPM_SORT: Final[str] = "bpm"
KEY_SORT: Final[str] = "key"
REVIEW_SORT: Final[str] = "review"

# One kind per library table column, in the column order of the table.
SORT_KINDS: Final[tuple[str, ...]] = (
    TEXT_SORT,
    TEXT_SORT,
    DURATION_SORT,
    TEXT_SORT,
    TEXT_SORT,
    TEXT_SORT,
    TEXT_SORT,
    BPM_SORT,
    KEY_SORT,
    REVIEW_SORT,
    TEXT_SORT,
)

# The pitch classes in the order of the piano keys, from C upward.
PITCH_CLASS_ORDER: Final[tuple[PitchClass, ...]] = (
    PitchClass.C,
    PitchClass.C_SHARP,
    PitchClass.D,
    PitchClass.E_FLAT,
    PitchClass.E,
    PitchClass.F,
    PitchClass.F_SHARP,
    PitchClass.G,
    PitchClass.A_FLAT,
    PitchClass.A,
    PitchClass.B_FLAT,
    PitchClass.B,
)

# The workflow order of the review statuses shown in the table.
REVIEW_STATUS_ORDER: Final[tuple[str, ...]] = (
    ReviewStatus.UNPROCESSED.value,
    ReviewStatus.PROPOSED.value,
    ReviewStatus.PARTIALLY_APPROVED.value,
    ReviewStatus.READY_TO_WRITE.value,
    ReviewStatus.WRITTEN.value,
    ReviewStatus.ERROR.value,
)

BLANK_TEXTS: Final[frozenset[str]] = frozenset({"", "-"})
_NUMBER_PATTERN = re.compile(r"(\d+)")


@dataclass(frozen=True)
class LibraryRow:
    """One library file with the values the table sorts on."""

    file_id: str
    cells: tuple[str, ...]
    path: str
    duration_seconds: float | None
    bpm: float | None
    key: tuple[PitchClass, KeyMode] | None
    review_status: str


@dataclass(frozen=True)
class _SortEntry:
    """One row with the compared values ready for the comparator."""

    row: LibraryRow
    group_rank: int
    primary: object
    mode_rank: int
    title_key: str
    path_key: str


def parse_bpm_value(text: str) -> float | None:
    """Return the BPM as a number, or None when the text holds no usable number.

    A value that is not a finite number, such as "NaN" or "inf", has no place
    in a numeric order. Such a value counts as no value at all.
    """
    try:
        value = float(text.strip())
    except (AttributeError, TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def natural_key(text: str) -> tuple[tuple[int, object], ...]:
    """Return a comparison key that orders the numbers inside a text by value.

    The text is folded to lower case. Each number becomes a token that sorts
    before each text token, so "Track 2" comes before "Track 10".

    A number compares by its digit count, then by its digits, with leading
    zeros removed. That order equals the numeric order. It needs no integer
    conversion, which Python refuses for a run above 4300 digits.
    """
    parts = _NUMBER_PATTERN.split(text.casefold())
    tokens: list[tuple[int, object]] = []
    for index, part in enumerate(parts):
        if index % 2:
            digits = part.lstrip("0")
            tokens.append((0, (len(digits), digits)))
        else:
            tokens.append((1, part))
    return tuple(tokens)


def _key_position(key: tuple[PitchClass, KeyMode], notation: KeyDisplayNotation) -> tuple[int, int]:
    """Return the wheel position and the mode rank of one key in the given notation."""
    if notation == KeyDisplayNotation.CAMELOT:
        label = format_canonical_key(key[0], key[1], KeyDisplayNotation.CAMELOT)
        return int(label[:-1]), 0 if label[-1] == "A" else 1
    if notation == KeyDisplayNotation.OPEN_KEY:
        label = format_canonical_key(key[0], key[1], KeyDisplayNotation.OPEN_KEY)
        return int(label[:-1]), 0 if label[-1] == "m" else 1
    position = PITCH_CLASS_ORDER.index(key[0])
    return position, 0 if key[1] == KeyMode.MINOR else 1


def _is_unsorted(row: LibraryRow, kind: str, column: int) -> bool:
    """Return True when the row holds no usable value for the column."""
    if kind == TEXT_SORT:
        return row.cells[column].strip() in BLANK_TEXTS
    if kind == DURATION_SORT:
        return row.duration_seconds is None
    if kind == BPM_SORT:
        return row.bpm is None
    if kind == KEY_SORT:
        return row.key is None
    return row.review_status not in REVIEW_STATUS_ORDER


def _primary_value(
    row: LibraryRow, kind: str, column: int, notation: KeyDisplayNotation
) -> tuple[object, int]:
    """Return the compared value and the mode rank for one row and column."""
    if kind == TEXT_SORT:
        return natural_key(row.cells[column]), 0
    if kind == DURATION_SORT:
        return float(row.duration_seconds or 0.0), 0
    if kind == BPM_SORT:
        return float(row.bpm or 0.0), 0
    if kind == KEY_SORT:
        assert row.key is not None
        position, mode_rank = _key_position(row.key, notation)
        return position, mode_rank
    return float(REVIEW_STATUS_ORDER.index(row.review_status)), 0


def _group_rank(unsorted: bool, placement: str) -> int:
    """Return the rank of the unsorted group against the rows that hold a value.

    The placement setting gives the unsorted group the lowest or the highest
    value, so the group moves with the sort direction.
    """
    if placement == UNSORTED_PLACEMENT_LAST:
        return 1 if unsorted else 0
    return 0 if unsorted else 1


def _build_entry(
    row: LibraryRow,
    kind: str,
    column: int,
    placement: str,
    notation: KeyDisplayNotation,
) -> _SortEntry:
    """Build the compared values for one row."""
    unsorted = _is_unsorted(row, kind, column)
    primary, mode_rank = _primary_value(row, kind, column, notation)
    return _SortEntry(
        row=row,
        group_rank=_group_rank(unsorted, placement),
        primary=primary,
        mode_rank=mode_rank,
        title_key=row.cells[TITLE_COLUMN].casefold(),
        path_key=row.path.casefold(),
    )


def _compare_values(left: object, right: object) -> int:
    """Return -1, 0, or 1 for two comparable values."""
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def _compare(left: _SortEntry, right: _SortEntry, descending: bool) -> int:
    """Compare two rows. The direction applies to the group and the column value."""
    direction = -1 if descending else 1
    result = _compare_values(left.group_rank, right.group_rank) * direction
    if result:
        return result
    result = _compare_values(left.primary, right.primary) * direction
    if result:
        return result
    result = _compare_values(left.mode_rank, right.mode_rank)
    if result:
        return result
    result = _compare_values(left.title_key, right.title_key)
    if result:
        return result
    return _compare_values(left.path_key, right.path_key)


def sort_rows(
    rows: list[LibraryRow],
    *,
    column: int,
    descending: bool,
    placement: str,
    notation: KeyDisplayNotation,
) -> list[LibraryRow]:
    """Return the rows in the order the table shows for one column.

    Rows that hold no value for the column form one group. The placement
    setting gives that group the lowest or the highest value. Rows that tie
    order by title, then by path.
    """
    kind = SORT_KINDS[column]
    entries = [_build_entry(row, kind, column, placement, notation) for row in rows]
    entries.sort(key=cmp_to_key(lambda left, right: _compare(left, right, descending)))
    return [entry.row for entry in entries]
