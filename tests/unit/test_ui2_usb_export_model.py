"""Tests for the plain texts and values of the two-step USB export dialog."""

from __future__ import annotations

import pytest

from infiniteprep.services.usb.preflight import FACT_ACTIVE_PARTITION, evaluate_preflight
from infiniteprep.ui.usb_export_model import (
    NO_DRIVE_CHANGES_TEXT,
    SPACE_BAR_STEPS,
    UNKNOWN_FREE_TEXT,
    detail_rows,
    drive_change_lines,
    drive_title,
    format_size,
    space_bar_value,
    space_summary,
)
from tests.unit.usb_preflight_support import (
    MOUNT_PATH,
    build_drive,
    build_inspection,
    build_partition,
    build_request,
)

ONE_GB = 1_000_000_000


@pytest.mark.parametrize(
    ("byte_count", "expected"),
    [
        (999, "999 bytes"),
        (1_500, "1.5 KB"),
        (31_250_000, "31.2 MB"),
        (16 * ONE_GB, "16.0 GB"),
        (2_000 * ONE_GB, "2.0 TB"),
    ],
)
def test_sizes_read_in_decimal_units(byte_count: int, expected: str) -> None:
    """Sizes use the decimal units drive makers print on the box."""
    assert format_size(byte_count) == expected


def test_space_summary_names_need_and_free() -> None:
    """The summary states the space the export needs against the free space."""
    result = evaluate_preflight(build_inspection(), build_request())
    needed = result.estimate_bytes + result.reserve_bytes

    assert space_summary(result) == (
        f"{format_size(needed)} needed of {format_size(16 * ONE_GB)} free"
    )


def test_space_summary_without_free_space() -> None:
    """A drive that reports no free space says so plainly."""
    result = evaluate_preflight(build_inspection(available_bytes=None), build_request())

    assert space_summary(result) == UNKNOWN_FREE_TEXT


def test_space_bar_fills_by_share_and_caps() -> None:
    """The bar fills by the needed share of free space and stops at full."""
    assert space_bar_value(250, 1_000) == SPACE_BAR_STEPS // 4
    assert space_bar_value(5_000, 1_000) == SPACE_BAR_STEPS
    assert space_bar_value(10, 0) == SPACE_BAR_STEPS
    assert space_bar_value(10, None) is None


def test_drive_title_prefers_the_volume_name() -> None:
    """The drive shows its volume name, then its model, then its device name."""
    assert drive_title(build_inspection()) == "STICK"
    unnamed = build_inspection(partitions=(build_partition(label=None),))
    assert drive_title(unnamed) == "Ultra USB 3.0"
    bare = build_inspection(
        drive=build_drive(model=None), partitions=(build_partition(label=None),)
    )
    assert drive_title(bare) == "sdb"


def test_detail_rows_hold_identifiers_and_unchecked_facts() -> None:
    """The details list the identifiers and each fact the check could not confirm."""
    inspection = build_inspection(partitions=(build_partition(is_active=None),))
    result = evaluate_preflight(inspection, build_request())

    rows = dict(detail_rows(inspection, result))

    assert result.unverified_facts == (FACT_ACTIVE_PARTITION,)
    assert rows["Location"] == MOUNT_PATH
    assert rows["Model"] == "Ultra USB 3.0"
    assert rows["Device ID"] == "0781:5581"
    assert rows["Serial number"] == "1A2B-3C4D"
    assert rows["Couldn't check"] == "Active partition"


def test_drive_change_lines_or_the_empty_sentence() -> None:
    """The planned changes show as given. No changes show one plain sentence."""
    assert drive_change_lines(build_request()) == ["Add 2 tracks", "Update 1 playlist"]
    assert drive_change_lines(build_request(planned_changes=())) == [NO_DRIVE_CHANGES_TEXT]
