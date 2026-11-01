"""Behavioral tests for the ID3v2 codec, built from byte layouts in the ID3v2 structure docs."""

from __future__ import annotations

import pytest

from infiniteprep.adapters.metadata_io.id3v2 import (
    Id3FormatError,
    Id3Frame,
    Id3Tag,
    decode_text_values,
    decode_txxx,
    frame_key,
    parse_id3v2,
    render_id3v23,
    replace_text_frame,
    replace_txxx,
)
from tests.fixtures.id3v2_bytes import synchsafe, tag_bytes, utf16, v22_frame, v23_frame, v24_frame


def test_parse_returns_none_without_id3_magic() -> None:
    """Data that does not start with an ID3 header has no tag."""
    assert parse_id3v2(b"\xff\xfb\x90\x00" + b"\x00" * 32) is None


def test_v23_text_and_txxx_frames_decode() -> None:
    """UTF-16 text and TXXX frames decode to their values."""
    frames = v23_frame("TIT2", b"\x01" + utf16("Strobe")) + v23_frame(
        "TXXX", b"\x01" + utf16("CATALOGNUMBER") + b"\x00\x00" + utf16("MAU5-001")
    )
    tag = parse_id3v2(tag_bytes(3, frames))
    assert tag is not None
    assert tag.source_version == 3
    title, txxx = tag.frames
    assert decode_text_values(title) == ["Strobe"]
    assert decode_txxx(txxx) == ("CATALOGNUMBER", ["MAU5-001"])
    assert frame_key(txxx) == "TXXX:CATALOGNUMBER"


def test_v24_utf8_multi_value_text_decodes() -> None:
    """ID3v2.4 null-separated UTF-8 values decode as separate strings."""
    tag = parse_id3v2(tag_bytes(4, v24_frame("TCON", b"\x03House\x00Techno\x00")))
    assert tag is not None
    assert decode_text_values(tag.frames[0]) == ["House", "Techno"]


def test_v22_frames_upgrade_to_four_character_ids() -> None:
    """ID3v2.2 frames convert to their ID3v2.3 identifiers."""
    tag = parse_id3v2(tag_bytes(2, v22_frame("TT2", b"\x00Title") + v22_frame("TBP", b"\x00128")))
    assert tag is not None
    assert [frame.frame_id for frame in tag.frames] == ["TIT2", "TBPM"]
    assert decode_text_values(tag.frames[1]) == ["128"]


def test_v23_tag_unsynchronisation_is_reversed() -> None:
    """A v2.3 tag with the unsynchronisation flag drops each 0x00 that follows 0xFF."""
    body = b"\x00\xff\x00\xe0"
    raw = v23_frame("PRIV", b"owner\x00" + body)
    unsynced = raw.replace(b"\xff\x00\xe0", b"\xff\x00\x00\xe0")
    tag = parse_id3v2(tag_bytes(3, unsynced, flags=0x80))
    assert tag is not None
    assert tag.frames[0].body == b"owner\x00\x00\xff\x00\xe0"


def test_truncated_tag_raises_format_error() -> None:
    """A header size larger than the data raises Id3FormatError."""
    raw = tag_bytes(3, v23_frame("TIT2", b"\x00Title"))
    with pytest.raises(Id3FormatError):
        parse_id3v2(raw[:-8])


def test_unsupported_major_version_raises_format_error() -> None:
    """Major versions outside 2 to 4 raise Id3FormatError."""
    with pytest.raises(Id3FormatError):
        parse_id3v2(b"ID3\x05\x00\x00" + synchsafe(0))


def test_render_round_trip_keeps_unknown_frames_byte_for_byte() -> None:
    """Rendering as v2.3 and parsing again keeps unknown frame bodies unchanged."""
    priv_body = b"TRAKTOR4\x00" + bytes(range(256))
    source = parse_id3v2(tag_bytes(3, v23_frame("PRIV", priv_body)))
    assert source is not None
    edited = replace_text_frame(source, "TBPM", "124.00")
    rendered = render_id3v23(edited)
    assert rendered[:5] == b"ID3\x03\x00"
    reparsed = parse_id3v2(rendered)
    assert reparsed is not None
    assert reparsed.frames[0] == Id3Frame("PRIV", priv_body)
    assert decode_text_values(reparsed.frames[1]) == ["124.00"]


def test_render_converts_v24_text_to_utf16_and_tdrc_to_tyer() -> None:
    """v2.4 UTF-8 text is re-encoded as UTF-16 and TDRC becomes TYER in v2.3 output."""
    frames = v24_frame("TPE1", b"\x03Ch\xc3\xa9 Artist\x00") + v24_frame(
        "TDRC", b"\x032021-05-14\x00"
    )
    source = parse_id3v2(tag_bytes(4, frames))
    assert source is not None
    reparsed = parse_id3v2(render_id3v23(source))
    assert reparsed is not None
    by_id = {frame.frame_id: frame for frame in reparsed.frames}
    assert "TDRC" not in by_id
    assert by_id["TPE1"].body[0] == 1
    assert decode_text_values(by_id["TPE1"]) == ["Ché Artist"]
    assert decode_text_values(by_id["TYER"]) == ["2021"]
    assert decode_text_values(by_id["TDAT"]) == ["1405"]


def test_render_joins_multiple_values_with_slash() -> None:
    """ID3v2.3 has one string per text frame, so values join with a slash."""
    source = parse_id3v2(tag_bytes(4, v24_frame("TCON", b"\x03House\x00Techno")))
    assert source is not None
    reparsed = parse_id3v2(render_id3v23(source))
    assert reparsed is not None
    assert decode_text_values(reparsed.frames[0]) == ["House/Techno"]


def test_replace_helpers_drop_old_frames_with_the_same_key() -> None:
    """Replacing a frame removes every frame with that key and keeps other TXXX frames."""
    tag = Id3Tag(source_version=3)
    tag = replace_text_frame(tag, "TKEY", "8A")
    tag = replace_text_frame(tag, "TKEY", "9A")
    tag = replace_txxx(tag, "STYLE", "Deep")
    tag = replace_txxx(tag, "VINYLTRACK", "A1")
    tag = replace_txxx(tag, "STYLE", "Minimal")
    keys = [frame_key(frame) for frame in tag.frames]
    assert keys == ["TKEY", "TXXX:VINYLTRACK", "TXXX:STYLE"]
    assert decode_text_values(tag.frames[0]) == ["9A"]
    assert decode_txxx(tag.frames[2]) == ("STYLE", ["Minimal"])
