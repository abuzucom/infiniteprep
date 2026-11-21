"""Behavioral tests for leap seconds, fractional seconds, and invalid dates on v2.3 render."""

from __future__ import annotations

import pytest

from tests.fixtures.id3v2_render import render_v24_timestamp


def test_leap_second_keeps_time() -> None:
    """Second 60 marks a UTC leap second, so TIME keeps the hour and minutes."""
    values = render_v24_timestamp("2016-12-31T23:59:60Z")
    assert values == {"TYER": ["2016"], "TDAT": ["3112"], "TIME": ["2359"]}


@pytest.mark.parametrize("fraction", [".1", ".123", ".123456"])
def test_fractional_seconds_parse_and_drop(fraction: str) -> None:
    """A fraction after the seconds parses, and the v2.3 frames carry no trace of it."""
    values = render_v24_timestamp(f"2021-05-14T10:30:00{fraction}Z")
    assert values == {"TYER": ["2021"], "TDAT": ["1405"], "TIME": ["1030"]}


@pytest.mark.parametrize("malformed", ["2021-05-14T10:30:00.", "2021-05-14T10:30:00.5x"])
def test_malformed_fraction_writes_no_date(malformed: str) -> None:
    """A dot with no digits, or trailing text after a fraction, keeps the TDRC frame verbatim."""
    assert render_v24_timestamp(malformed) == {"TDRC": [malformed]}


@pytest.mark.parametrize("timestamp", ["2021-02-30T10:30", "2021-13-01T10:30:00Z"])
def test_invalid_calendar_date_drops_time(timestamp: str) -> None:
    """A time whose date does not exist writes the year alone, with no TDAT or TIME."""
    assert render_v24_timestamp(timestamp) == {"TYER": ["2021"]}
