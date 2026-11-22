"""Sorting keeps working when a row holds no usable value for the sorted column."""

from __future__ import annotations

import unittest

from infiniteprep.config.settings import UNSORTED_PLACEMENT_LAST
from infiniteprep.domain.enums import KeyDisplayNotation, KeyMode, PitchClass, ReviewStatus
from infiniteprep.ui.table_sort import (
    KEY_COLUMN,
    REVIEW_STATUS_COLUMN,
    SORT_COLUMN_COUNT,
    TITLE_COLUMN,
    LibraryRow,
    natural_key,
    sort_rows,
)

# Python refuses int() on a digit run longer than this default limit.
INT_DIGIT_LIMIT: int = 4300


def make_row(
    name: str,
    *,
    key: tuple[PitchClass, KeyMode] | None = None,
    review_status: str = ReviewStatus.UNPROCESSED.value,
) -> LibraryRow:
    """Build one library row whose title cell holds the name."""
    cells = [""] * SORT_COLUMN_COUNT
    cells[TITLE_COLUMN] = name
    return LibraryRow(
        file_id=name,
        cells=tuple(cells),
        path=f"/crate/{name}.wav",
        duration_seconds=None,
        bpm=None,
        key=key,
        review_status=review_status,
    )


def sorted_ids(rows: list[LibraryRow], column: int) -> list[str]:
    """Return the file ids in ascending table order for one column."""
    ordered = sort_rows(
        rows,
        column=column,
        descending=False,
        placement=UNSORTED_PLACEMENT_LAST,
        notation=KeyDisplayNotation.CAMELOT,
    )
    return [row.file_id for row in ordered]


class UnsortedRowSortTests(unittest.TestCase):
    """Rows without a value form the unsorted group and never break the sort."""

    def test_key_sort_places_a_keyless_row_last(self) -> None:
        """A row without a key sorts after every row with a key."""
        rows = [
            make_row("keyless"),
            make_row("c-major", key=(PitchClass.C, KeyMode.MAJOR)),
            make_row("a-minor", key=(PitchClass.A, KeyMode.MINOR)),
        ]

        ordered = sorted_ids(rows, KEY_COLUMN)

        self.assertEqual(ordered[-1], "keyless")
        self.assertEqual(set(ordered[:2]), {"c-major", "a-minor"})

    def test_status_sort_puts_unlisted_last(self) -> None:
        """Statuses outside the sort order follow the listed statuses."""
        rows = [
            make_row("no-match", review_status=ReviewStatus.NO_MATCH.value),
            make_row("written", review_status=ReviewStatus.WRITTEN.value),
            make_row("ready", review_status=ReviewStatus.READY_FOR_REVIEW.value),
            make_row("unprocessed", review_status=ReviewStatus.UNPROCESSED.value),
        ]

        ordered = sorted_ids(rows, REVIEW_STATUS_COLUMN)

        self.assertEqual(ordered[:2], ["unprocessed", "written"])
        self.assertEqual(ordered[2:], ["no-match", "ready"])


class NaturalKeyTests(unittest.TestCase):
    """The natural key orders numbers by value at any length."""

    def test_numbers_order_by_value(self) -> None:
        """Digit runs compare by value, and leading zeros and case make no difference."""
        self.assertLess(natural_key("Track 2"), natural_key("Track 10"))
        self.assertLess(natural_key("Track 09"), natural_key("Track 10"))
        self.assertEqual(natural_key("Track 007"), natural_key("track 7"))

    def test_digit_run_past_the_int_limit_still_compares(self) -> None:
        """A digit run longer than the int conversion limit still sorts after a short number."""
        long_run = "9" * (INT_DIGIT_LIMIT + 1)

        self.assertLess(natural_key("mix 5"), natural_key(f"mix {long_run}"))


if __name__ == "__main__":
    unittest.main()
