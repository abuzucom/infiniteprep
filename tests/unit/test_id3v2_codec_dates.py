"""Behavioral tests for ID3v2.4 timestamp conversion when the codec renders ID3v2.3."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from infiniteprep.adapters.metadata_io.id3v2 import (
    decode_text_values,
    parse_id3v2,
    render_id3v23,
)
from tests.fixtures.id3v2_bytes import tag_bytes, v24_frame


def v24_utf8_frame(frame_id: str, text: str) -> bytes:
    """Build one ID3v2.4 UTF-8 text frame with a synchsafe size."""
    return v24_frame(frame_id, b"\x03" + text.encode("utf-8") + b"\x00")


def rendered_frames(frames: bytes) -> dict[str, list[str]]:
    """Wrap frames in a v2.4 tag, render it as v2.3, and map identifiers to their values."""
    source = parse_id3v2(tag_bytes(4, frames))
    assert source is not None
    reparsed = parse_id3v2(render_id3v23(source))
    assert reparsed is not None
    values: dict[str, list[str]] = {}
    for frame in reparsed.frames:
        values.setdefault(frame.frame_id, []).extend(decode_text_values(frame))
    return values


def test_unparseable_tdrc_is_kept_instead_of_dropped() -> None:
    """A TDRC value without a leading year stays in the output as its own frame."""
    values = rendered_frames(v24_utf8_frame("TDRC", "sometime in 2021"))
    assert values == {"TDRC": ["sometime in 2021"]}


def test_unparseable_tdor_is_kept_instead_of_dropped() -> None:
    """A TDOR value without a leading year stays in the output as its own frame."""
    values = rendered_frames(v24_utf8_frame("TDOR", "unknown"))
    assert values == {"TDOR": ["unknown"]}


def test_out_of_range_date_and_time_parts_are_not_written() -> None:
    """Month 99, day 99, and time 99:99 produce no TDAT or TIME frame. The year still converts."""
    values = rendered_frames(v24_utf8_frame("TDRC", "2021-99-99T99:99"))
    assert values == {"TYER": ["2021"]}


def test_out_of_range_time_keeps_a_valid_date() -> None:
    """Hour 25 drops only TIME. A valid month and day still produce TDAT."""
    values = rendered_frames(v24_utf8_frame("TDRC", "2021-05-14T25:30"))
    assert values == {"TYER": ["2021"], "TDAT": ["1405"]}


def test_zero_month_or_day_is_not_written() -> None:
    """Month 00 is outside 1 to 12, so no TDAT frame appears."""
    values = rendered_frames(v24_utf8_frame("TDRC", "2021-00-14"))
    assert values == {"TYER": ["2021"]}


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    """Load standalone test functions into the unittest suite."""
    suite = unittest.TestSuite()
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func) and name != "load_tests":
            suite.addTest(unittest.FunctionTestCase(func))
    return suite

