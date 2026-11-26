"""Behavioral tests for the phase 5 hygiene changes of issue 288."""

from __future__ import annotations

import pytest

from infiniteprep.services.usb._shared import deduplicate_space_entries
from infiniteprep.services.usb.models import SpaceInfo
from infiniteprep.ui.folder_import import (
    STUCK_FILE_TIMEOUT_MS,
    STUCK_TEXT,
    stuck_import_text,
)
from infiniteprep.ui.time_format import split_time_seconds

PREVIOUS_STUCK_TEXT = (
    "One file has taken more than 2 minutes to import. Quit now closes InfinitePrep "
    "right away and keeps nothing from this import. Keep waiting lets the import continue."
)


@pytest.mark.parametrize(
    ("timeout_ms", "expected"),
    [
        (120_000, "more than 2 minutes to import."),
        (60_000, "more than 1 minute to import."),
        (90_000, "more than 90 seconds to import."),
        (5_000, "more than 5 seconds to import."),
        (1_000, "more than 1 second to import."),
        (400, "more than 1 second to import."),
    ],
)
def test_stuck_import_text_names_timeout(timeout_ms: int, expected: str) -> None:
    """The stuck import text names the timeout in whole minutes or seconds."""
    assert expected in stuck_import_text(timeout_ms)


def test_default_stuck_text_keeps_wording() -> None:
    """The default timeout gives the same stuck text as the earlier fixed wording."""
    assert stuck_import_text(STUCK_FILE_TIMEOUT_MS) == PREVIOUS_STUCK_TEXT
    assert STUCK_TEXT == PREVIOUS_STUCK_TEXT


def test_space_entries_first_per_mount() -> None:
    """A repeated mount drops its later space entry and the order stays the same."""
    first = SpaceInfo(total_bytes=100, used_bytes=50, available_bytes=50)
    second = SpaceInfo(total_bytes=200, used_bytes=120, available_bytes=80)
    repeat = SpaceInfo(total_bytes=999, used_bytes=998, available_bytes=1)
    entries = [("/media/a", first), ("/media/b", second), ("/media/a", repeat)]
    assert deduplicate_space_entries(entries) == (("/media/a", first), ("/media/b", second))


@pytest.mark.parametrize(
    ("total_seconds", "expected"),
    [(0, (0, 0, 0)), (59, (0, 0, 59)), (3_599, (0, 59, 59)), (3_661, (1, 1, 1))],
)
def test_split_time_seconds_carries_hours(
    total_seconds: int, expected: tuple[int, int, int]
) -> None:
    """Whole seconds split into hours, minutes, and seconds."""
    assert split_time_seconds(total_seconds) == expected
