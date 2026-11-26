"""Tests that crafted tag data costs bounded time and memory in the tag parsers and the sort."""

from __future__ import annotations

import time

import pytest

from infiniteprep.adapters.metadata_io.id3v2 import (
    LATIN1,
    MAX_TEXT_BYTES,
    MAX_TEXT_VALUES,
    UTF16,
    Id3FormatError,
    Id3Frame,
    Id3Tag,
    decode_description,
    decode_text_values,
    decode_txxx,
    render_id3v23,
)
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import _picture_fields
from infiniteprep.adapters.metadata_io.ogg_reader import (
    MAX_PACKET_SIZE,
    OggFormatError,
    _extract_page_packets,
)
from infiniteprep.ui.table_sort import natural_key

FULL_SEGMENT = 255
# About 4 MiB of full segments. The quadratic size check needed over 100 million steps here.
LONG_PACKET_SEGMENTS = 16_000
LINEAR_TIME_LIMIT_SECONDS = 2.0
# Python refuses int() for a digit run above 4300 digits.
LONG_DIGIT_RUN = 5_000
PICTURE_BYTES = MAX_TEXT_BYTES + 1024


def _latin1_frame(frame_id: str, text: bytes) -> Id3Frame:
    """Return a frame whose body holds the Latin-1 encoding byte and text."""
    return Id3Frame(frame_id, bytes((LATIN1,)) + text)


def test_text_at_value_cap_decodes() -> None:
    """A frame with exactly the allowed number of values keeps all of them."""
    frame = _latin1_frame("TPE1", b"a\x00" * (MAX_TEXT_VALUES - 1) + b"last")

    values = decode_text_values(frame)

    assert len(values) == MAX_TEXT_VALUES
    assert values[-1] == "last"


def test_text_over_value_cap_rejected() -> None:
    """One value past the cap raises instead of building an unbounded list."""
    frame = _latin1_frame("TPE1", b"a\x00" * MAX_TEXT_VALUES + b"last")

    with pytest.raises(Id3FormatError, match="more than"):
        decode_text_values(frame)


def test_text_over_byte_cap_rejected() -> None:
    """A text frame larger than the byte cap raises before any split."""
    frame = _latin1_frame("TIT2", b"a" * (MAX_TEXT_BYTES + 1))

    with pytest.raises(Id3FormatError, match="exceeds"):
        decode_text_values(frame)


def test_txxx_over_value_cap_rejected() -> None:
    """The value cap also covers the values that follow a TXXX description."""
    frame = _latin1_frame("TXXX", b"desc\x00" + b"a\x00" * (MAX_TEXT_VALUES + 1))

    with pytest.raises(Id3FormatError, match="more than"):
        decode_txxx(frame)


def test_renderer_rejects_text_past_cap() -> None:
    """The v2.3 renderer raises for an oversized text frame and never rewrites it."""
    frame = _latin1_frame("TPE1", b"a\x00" * MAX_TEXT_VALUES + b"last")

    with pytest.raises(Id3FormatError, match="more than"):
        render_id3v23(Id3Tag(frames=(frame,)))


def test_utf16_terminator_needs_even_offset() -> None:
    """A zero pair that straddles two code units does not end a UTF-16 value."""
    # The code units 0x0041 and 0x0100 encode as 41 00 00 01. The zero pair in the middle
    # sits at an odd offset.
    first = bytes((0x41, 0x00, 0x00, 0x01))
    body = bytes((UTF16,)) + b"\xff\xfe" + first + b"\x00\x00"
    body += b"\xff\xfe" + "B".encode("utf-16-le")

    assert decode_text_values(Id3Frame("TPE1", body)) == [first.decode("utf-16-le"), "B"]


def test_description_ignores_trailing_bytes() -> None:
    """decode_description returns the description of a body with a large binary tail."""
    body = bytes((LATIN1,)) + b"Cover\x00" + b"\x00\x01" * (PICTURE_BYTES // 2)

    assert decode_description(body) == "Cover"


def test_large_picture_keeps_description() -> None:
    """Picture bytes never count as text, so a large picture keeps its MIME and description."""
    picture = b"\x00\xff" * (PICTURE_BYTES // 2)
    body = bytes((LATIN1,)) + b"image/png\x00" + b"\x03" + b"Front\x00" + picture

    assert _picture_fields(body) == ("image/png", "Front")


def test_long_ogg_packet_assembles_linearly() -> None:
    """A packet of thousands of full segments assembles well inside the time limit."""
    lacing = [FULL_SEGMENT] * LONG_PACKET_SEGMENTS + [1]
    body = b"\x01" * (FULL_SEGMENT * LONG_PACKET_SEGMENTS + 1)
    pending: list[bytes] = []
    packets: list[bytes] = []

    started = time.perf_counter()
    _extract_page_packets(lacing, body, pending, packets)
    elapsed = time.perf_counter() - started

    assert packets == [body]
    assert pending == []
    assert elapsed < LINEAR_TIME_LIMIT_SECONDS


def test_ogg_cap_counts_earlier_pages() -> None:
    """A fragment carried over from an earlier page counts toward the packet size cap."""
    pending = [b"\x01" * MAX_PACKET_SIZE]
    packets: list[bytes] = []

    with pytest.raises(OggFormatError, match="Packet size exceeds"):
        _extract_page_packets([1], b"\x01", pending, packets)


def test_ogg_size_resets_per_packet() -> None:
    """Two packets on one page each count their own bytes against the cap."""
    first = b"\x01" * 10
    second = b"\x02" * 20
    packets: list[bytes] = []

    _extract_page_packets([10, 20], first + second, [], packets)

    assert packets == [first, second]


def test_natural_key_accepts_huge_digits() -> None:
    """A digit run that int() refuses still yields a comparable key."""
    long_run = "9" * LONG_DIGIT_RUN

    assert natural_key(f"Track {long_run}") > natural_key("Track 10")


def test_natural_key_orders_unicode_digits() -> None:
    """A number in Arabic-Indic digits orders by its value against ASCII numbers."""
    arabic_zero = "\N{ARABIC-INDIC DIGIT ZERO}"
    arabic_two = "\N{ARABIC-INDIC DIGIT TWO}"

    assert natural_key(f"Track {arabic_two}") < natural_key("Track 9")
    assert natural_key(f"Track {arabic_two}") == natural_key("Track 2")
    assert natural_key(f"Track {arabic_zero}") == natural_key("Track 0")
    assert natural_key(f"Track {arabic_zero}{arabic_two}") == natural_key("Track 2")


def test_natural_key_orders_numbers_numerically() -> None:
    """Numbers inside text order by value, and leading zeros do not change the value."""
    assert natural_key("Track 2") < natural_key("Track 10")
    assert natural_key("Track 007") == natural_key("Track 7")
    assert natural_key("Track 0") == natural_key("Track 000")
    assert natural_key("Track 0") < natural_key("Track 1")
