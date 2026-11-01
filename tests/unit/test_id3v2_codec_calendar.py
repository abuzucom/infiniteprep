"""Behavioral tests for calendar and format validation of ID3v2.4 timestamps on v2.3 render."""

from __future__ import annotations

import pytest

from infiniteprep.adapters.metadata_io.id3v2 import decode_text_values, parse_id3v2, render_id3v23
from tests.fixtures.id3v2_bytes import tag_bytes, v24_frame


def render_tdrc(timestamp: str) -> dict[str, list[str]]:
    """Render a v2.4 tag holding one UTF-8 TDRC frame as v2.3 and map identifiers to values."""
    frame = v24_frame("TDRC", b"\x03" + timestamp.encode("utf-8") + b"\x00")
    source = parse_id3v2(tag_bytes(4, frame))
    assert source is not None
    reparsed = parse_id3v2(render_id3v23(source))
    assert reparsed is not None
    values: dict[str, list[str]] = {}
    for rendered in reparsed.frames:
        values.setdefault(rendered.frame_id, []).extend(decode_text_values(rendered))
    return values


@pytest.mark.parametrize("timestamp", ["2021-02-29", "2021-02-30", "2021-04-31", "2100-02-29"])
def test_impossible_calendar_date_writes_no_tdat(timestamp: str) -> None:
    """A day past the end of its month, leap years included, keeps the year and drops TDAT."""
    assert render_tdrc(timestamp) == {"TYER": [timestamp[:4]]}


@pytest.mark.parametrize(
    ("timestamp", "tdat"),
    [
        ("2020-02-29", "2902"),
        ("2000-02-29", "2902"),
        ("2021-04-30", "3004"),
        ("2021-12-31", "3112"),
    ],
)
def test_last_day_of_month_writes_tdat(timestamp: str, tdat: str) -> None:
    """The last valid day of a month, including a leap February 29, still produces TDAT."""
    assert render_tdrc(timestamp) == {"TYER": [timestamp[:4]], "TDAT": [tdat]}


@pytest.mark.parametrize(
    "timestamp", ["2021-05-14T10:30INVALID", "2021-05-14 extra", "20211", "2021-0514"]
)
def test_timestamp_with_trailing_text_is_kept_verbatim(timestamp: str) -> None:
    """Text after a timestamp makes it unparseable, so the TDRC frame stays as it is."""
    assert render_tdrc(timestamp) == {"TDRC": [timestamp]}


def test_hour_without_minutes_writes_no_time() -> None:
    """The v2.4 form yyyy-MM-ddTHH converts the date, and TIME needs both hour and minutes."""
    assert render_tdrc("2021-05-14T10") == {"TYER": ["2021"], "TDAT": ["1405"]}


def test_invalid_seconds_write_no_time() -> None:
    """Seconds outside 0 to 59 make the time invalid, so no TIME frame appears."""
    assert render_tdrc("2021-05-14T10:30:99") == {"TYER": ["2021"], "TDAT": ["1405"]}


@pytest.mark.parametrize("suffix", ["Z", "+02:00", "-0530"])
def test_utc_designator_or_offset_keeps_wall_clock_time(suffix: str) -> None:
    """A trailing Z or offset parses, and TIME keeps the written hour and minutes."""
    values = render_tdrc(f"2021-05-14T10:30{suffix}")
    assert values == {"TYER": ["2021"], "TDAT": ["1405"], "TIME": ["1030"]}
