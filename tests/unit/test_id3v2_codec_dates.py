"""Behavioral tests for ID3v2.4 timestamp conversion when the codec renders ID3v2.3."""

from __future__ import annotations

import pytest

from tests.fixtures.id3v2_render import build_v24_utf8_frame, render_v24_frames


def test_unparseable_tdrc_is_kept_instead_of_dropped() -> None:
    """A TDRC value without a leading year stays in the output as its own frame."""
    values = render_v24_frames(build_v24_utf8_frame("TDRC", "sometime in 2021"))
    assert values == {"TDRC": ["sometime in 2021"]}


def test_unparseable_tdor_is_kept_instead_of_dropped() -> None:
    """A TDOR value without a leading year stays in the output as its own frame."""
    values = render_v24_frames(build_v24_utf8_frame("TDOR", "unknown"))
    assert values == {"TDOR": ["unknown"]}


def test_out_of_range_date_parts_not_written() -> None:
    """Month 99, day 99, and time 99:99 produce no TDAT or TIME frame. The year still converts."""
    values = render_v24_frames(build_v24_utf8_frame("TDRC", "2021-99-99T99:99"))
    assert values == {"TYER": ["2021"]}


def test_out_of_range_time_keeps_a_valid_date() -> None:
    """Hour 25 drops only TIME. A valid month and day still produce TDAT."""
    values = render_v24_frames(build_v24_utf8_frame("TDRC", "2021-05-14T25:30"))
    assert values == {"TYER": ["2021"], "TDAT": ["1405"]}


@pytest.mark.parametrize("timestamp", ["2021-00-14", "2021-05-00"])
def test_zero_month_or_day_is_not_written(timestamp: str) -> None:
    """A month or day of 00 forms no calendar date. The year converts without a TDAT frame."""
    values = render_v24_frames(build_v24_utf8_frame("TDRC", timestamp))
    assert values == {"TYER": ["2021"]}
