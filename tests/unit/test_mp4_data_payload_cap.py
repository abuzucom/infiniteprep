"""Tests that an oversized MP4 data payload shows as a size summary and is never decoded."""

from __future__ import annotations

import struct

import pytest

from infiniteprep.adapters.metadata_io import mp4_reader
from infiniteprep.adapters.metadata_io.mp4_reader import read_mp4
from tests.fixtures.mp4_bytes import (
    audio_entry,
    data_atom,
    esds,
    item,
    m4a_file,
    mvhd,
    trak,
    udta,
)

RATE_44K = 44_100
STEREO = 2
AAC_BITRATE = 256_000
SMALL_PAYLOAD_CAP = 64
TEXT_UTF8 = 1
SIGNED_INT = 21
JPEG = 13
COVER_BYTES = SMALL_PAYLOAD_CAP * 4
# Nine bytes exceed the longest integer an iTunes data atom holds.
LONG_INTEGER = bytes(9)


def _file_with(*items: bytes) -> bytes:
    """Return M4A bytes with one AAC track and the given ilst items."""
    track = trak(b"soun", audio_entry(b"mp4a", STEREO, RATE_44K, esds(AAC_BITRATE)))
    return m4a_file(mvhd(RATE_44K, RATE_44K), track, udta(*items))


def _tag_values(data: bytes) -> dict[str, tuple[str, ...]]:
    """Read data and map each tag key to its values."""
    return {tag.key: tag.values for tag in read_mp4(data).tags}


def test_oversize_text_becomes_size_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A text payload past the cap reads as its size, and a short payload still decodes."""
    monkeypatch.setattr(mp4_reader, "MAX_DATA_PAYLOAD_BYTES", SMALL_PAYLOAD_CAP)
    long_text = b"n" * (SMALL_PAYLOAD_CAP + 1)
    data = _file_with(
        item(b"\xa9cmt", data_atom(TEXT_UTF8, long_text)),
        item(b"\xa9ART", data_atom(TEXT_UTF8, b"Artist")),
    )

    values = _tag_values(data)

    assert values["\xa9cmt"] == (f"<oversized data {len(long_text)} bytes>",)
    assert values["\xa9ART"] == ("Artist",)


def test_text_payload_at_the_cap_still_decodes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A text payload exactly at the cap decodes to its text."""
    monkeypatch.setattr(mp4_reader, "MAX_DATA_PAYLOAD_BYTES", SMALL_PAYLOAD_CAP)
    text = b"m" * SMALL_PAYLOAD_CAP

    values = _tag_values(_file_with(item(b"\xa9cmt", data_atom(TEXT_UTF8, text))))

    assert values["\xa9cmt"] == (text.decode(),)


def test_cover_above_the_cap_keeps_its_cover_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """A picture past the payload cap still reads as a cover with its byte count."""
    monkeypatch.setattr(mp4_reader, "MAX_DATA_PAYLOAD_BYTES", SMALL_PAYLOAD_CAP)

    values = _tag_values(_file_with(item(b"covr", data_atom(JPEG, bytes(COVER_BYTES)))))

    assert values["covr"] == (f"<cover image/jpeg {COVER_BYTES} bytes>",)


def test_long_integer_payload_reads_as_binary() -> None:
    """An integer data atom longer than any iTunes integer reads as a binary summary."""
    short = item(b"tmpo", data_atom(SIGNED_INT, struct.pack(">H", 124)))
    long = item(b"plID", data_atom(SIGNED_INT, LONG_INTEGER))

    values = _tag_values(_file_with(short, long))

    assert values["tmpo"] == ("124",)
    assert values["plID"] == (f"<binary {len(LONG_INTEGER)} bytes>",)
