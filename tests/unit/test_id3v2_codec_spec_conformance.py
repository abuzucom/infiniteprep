"""Behavioral tests that check ID3v2 codec output against the id3v2.3.0 and id3v2.4.0 rules."""

from __future__ import annotations

import struct
import sys
import unittest
import zlib
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from infiniteprep.adapters.metadata_io.id3v2 import (
    decode_text_values,
    decode_txxx,
    parse_id3v2,
    render_id3v23,
)
from tests.fixtures.id3v2_bytes import (
    SYNC_BYTE,
    tag_bytes,
    unsynchronise,
    utf16,
    v23_frame,
    v24_frame,
)

TAG_FLAG_UNSYNC = 0x80
V23_COMPRESSED = b"\x00\x80"
FRAME_FLAGS = slice(18, 20)


def rendered_frames(source_bytes: bytes) -> dict[str, list[str]]:
    """Render a tag as v2.3, parse it again, and map each frame identifier to its values."""
    source = parse_id3v2(source_bytes)
    assert source is not None
    reparsed = parse_id3v2(render_id3v23(source))
    assert reparsed is not None
    values: dict[str, list[str]] = {}
    for frame in reparsed.frames:
        values.setdefault(frame.frame_id, []).extend(decode_text_values(frame))
    return values


def test_unsynchronisation_round_trip_restores_every_byte_after_0xff() -> None:
    """Reading reverses the writer scheme for 0xFF followed by each possible byte value."""
    payload = b"owner\x00" + b"".join(bytes((SYNC_BYTE, value)) for value in range(256))
    payload += b"\xff\x00\x00\xff\xff"
    frame = v23_frame("PRIV", payload)
    tag = parse_id3v2(tag_bytes(3, unsynchronise(frame), flags=TAG_FLAG_UNSYNC))
    assert tag is not None
    assert tag.dropped_frames == ()
    assert tag.frames[0].body == payload


def test_render_keeps_existing_tdat_instead_of_adding_a_second() -> None:
    """A tag that already holds TDAT keeps that one frame when TDRC converts."""
    frames = v24_frame("TDRC", b"\x032021-05-14T10:30\x00") + v24_frame("TDAT", b"\x030101\x00")
    values = rendered_frames(tag_bytes(4, frames))
    assert values["TDAT"] == ["0101"]
    assert values["TIME"] == ["1030"]
    assert values["TYER"] == ["2021"]


def test_render_truncates_tdrc_seconds_and_offset_to_v23_time() -> None:
    """v2.3 TIME holds HHMM, so TDRC seconds and a time zone offset do not reach it."""
    values = rendered_frames(tag_bytes(4, v24_frame("TDRC", b"\x032021-05-14T10:30:45+02:00\x00")))
    assert values["TYER"] == ["2021"]
    assert values["TDAT"] == ["1405"]
    assert values["TIME"] == ["1030"]


def test_render_joins_latin1_and_utf16_multi_values_with_slash() -> None:
    """Null-separated v2.4 values in Latin-1 or UTF-16 also join with a slash for v2.3."""
    frames = v24_frame("TCON", b"\x00House\x00Techno") + v24_frame(
        "TPE1", b"\x01" + utf16("Artist A") + b"\x00\x00" + utf16("Artist B")
    )
    values = rendered_frames(tag_bytes(4, frames))
    assert values["TCON"] == ["House/Techno"]
    assert values["TPE1"] == ["Artist A/Artist B"]


def test_render_strips_utf16be_comment_terminator() -> None:
    """A v2.4 UTF-16BE COMM frame becomes UTF-16 without trailing terminator characters."""
    body = b"\x02eng" + "Mood".encode("utf-16-be") + b"\x00\x00"
    body += "Warm".encode("utf-16-be") + b"\x00\x00"
    source = parse_id3v2(tag_bytes(4, v24_frame("COMM", body)))
    assert source is not None
    reparsed = parse_id3v2(render_id3v23(source))
    assert reparsed is not None
    assert reparsed.frames[0].body == b"\x01eng" + utf16("Mood") + b"\x00\x00" + utf16("Warm")


def test_render_writes_inflated_body_with_clear_format_flags() -> None:
    """A compressed source frame renders as its inflated body, so no format flag applies."""
    plain = b"\x00Compressed Title"
    raw = struct.pack(">I", len(plain)) + zlib.compress(plain)
    source = parse_id3v2(tag_bytes(3, v23_frame("TIT2", raw, V23_COMPRESSED)))
    assert source is not None
    rendered = render_id3v23(source)
    assert rendered[FRAME_FLAGS] == b"\x00\x00"
    reparsed = parse_id3v2(rendered)
    assert reparsed is not None
    assert reparsed.frames[0].body == plain


def test_render_keeps_description_only_txxx_without_a_value() -> None:
    """A TXXX frame with a description and no value still has no value after a rewrite."""
    source_bytes = tag_bytes(3, v23_frame("TXXX", b"\x01" + utf16("FLAG") + b"\x00\x00"))
    source = parse_id3v2(source_bytes)
    assert source is not None
    reparsed = parse_id3v2(render_id3v23(source))
    assert reparsed is not None
    assert decode_txxx(reparsed.frames[0]) == ("FLAG", [])


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    """Load standalone test functions into the unittest suite."""
    suite = unittest.TestSuite()
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func) and name != "load_tests":
            suite.addTest(unittest.FunctionTestCase(func))
    return suite

