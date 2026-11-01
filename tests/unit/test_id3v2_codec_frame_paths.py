"""Behavioral tests for ID3v2 frame-level paths: language frames, frame flags, and v2.2 upgrades."""

from __future__ import annotations

import struct
import zlib

import pytest

from infiniteprep.adapters.metadata_io.id3v2 import (
    Id3FormatError,
    Id3Frame,
    Id3Tag,
    decode_text_values,
    frame_key,
    parse_id3v2,
    remove_frames,
    render_id3v23,
)

UTF16_BOM_LE = b"\xff\xfe"
V23_COMPRESSED_AND_GROUPED = b"\x00\xa0"
V23_ENCRYPTED = b"\x00\x40"
# 200 stored as a plain integer sets the top bit of the last size byte.
PLAIN_SIZE_WITH_TOP_BIT = 200


def synchsafe(value: int) -> bytes:
    """Encode value as a 4-byte synchsafe integer."""
    return bytes((value >> shift) & 0x7F for shift in (21, 14, 7, 0))


def utf16(text: str) -> bytes:
    """Encode text as BOM-prefixed little-endian UTF-16."""
    return UTF16_BOM_LE + text.encode("utf-16-le")


def v23_frame(frame_id: str, body: bytes, flags: bytes = b"\x00\x00") -> bytes:
    """Build one ID3v2.3 frame with a plain big-endian size."""
    return frame_id.encode("ascii") + struct.pack(">I", len(body)) + flags + body


def v24_frame(frame_id: str, body: bytes) -> bytes:
    """Build one ID3v2.4 frame with a synchsafe size."""
    return frame_id.encode("ascii") + synchsafe(len(body)) + b"\x00\x00" + body


def v22_frame(frame_id: str, body: bytes) -> bytes:
    """Build one ID3v2.2 frame with a 3-byte size."""
    return frame_id.encode("ascii") + len(body).to_bytes(3, "big") + body


def tag_bytes(major: int, frames: bytes, flags: int = 0, padding: int = 16) -> bytes:
    """Wrap frames in an ID3v2 header of the given major version."""
    body = frames + b"\x00" * padding
    return b"ID3" + bytes((major, 0, flags)) + synchsafe(len(body)) + body


def test_comm_frame_key_names_description_and_language() -> None:
    """A COMM key holds the description and the 3-byte language code."""
    frame = Id3Frame("COMM", b"\x01eng" + utf16("Energy") + b"\x00\x00" + utf16("7"))
    assert frame_key(frame) == "COMM:Energy:eng"


def test_render_reencodes_v24_utf8_comment_as_utf16() -> None:
    """A v2.4 UTF-8 COMM frame becomes UTF-16 with its language and description kept."""
    source = parse_id3v2(tag_bytes(4, v24_frame("COMM", b"\x03engMood\x00Warm d\xc3\xa9but\x00")))
    assert source is not None
    reparsed = parse_id3v2(render_id3v23(source))
    assert reparsed is not None
    comment = reparsed.frames[0]
    assert comment.body == b"\x01eng" + utf16("Mood") + b"\x00\x00" + utf16("Warm début")
    assert frame_key(comment) == "COMM:Mood:eng"


def test_v23_compressed_grouped_frame_inflates() -> None:
    """A v2.3 frame with compression and grouping flags decodes to its plain body."""
    plain = b"\x00Compressed Title"
    raw = struct.pack(">I", len(plain)) + b"\x07" + zlib.compress(plain)
    tag = parse_id3v2(tag_bytes(3, v23_frame("TIT2", raw, V23_COMPRESSED_AND_GROUPED)))
    assert tag is not None
    assert tag.dropped_frames == ()
    assert tag.frames == (Id3Frame("TIT2", plain),)


def test_v23_encrypted_frame_is_dropped_and_named() -> None:
    """An encrypted v2.3 frame is not kept and appears in dropped_frames."""
    frames = v23_frame("PRIV", b"owner\x00\x80secret", V23_ENCRYPTED) + v23_frame(
        "TIT2", b"\x00Kept"
    )
    tag = parse_id3v2(tag_bytes(3, frames))
    assert tag is not None
    assert [frame.frame_id for frame in tag.frames] == ["TIT2"]
    assert tag.dropped_frames == ("PRIV",)


def test_v24_plain_integer_frame_size_with_top_bit_decodes() -> None:
    """A v2.4 frame size stored as a plain integer with a top bit set still reads in full."""
    body = b"owner\x00" + bytes(PLAIN_SIZE_WITH_TOP_BIT - len(b"owner\x00"))
    raw = b"PRIV" + struct.pack(">I", PLAIN_SIZE_WITH_TOP_BIT) + b"\x00\x00" + body
    tag = parse_id3v2(tag_bytes(4, raw + v24_frame("TIT2", b"\x03After\x00")))
    assert tag is not None
    assert tag.frames[0] == Id3Frame("PRIV", body)
    assert decode_text_values(tag.frames[1]) == ["After"]


def test_v22_unknown_frame_is_dropped_and_named() -> None:
    """A v2.2 frame without a v2.3 equivalent is not kept and appears in dropped_frames."""
    tag = parse_id3v2(tag_bytes(2, v22_frame("XYZ", b"data") + v22_frame("TT2", b"\x00Title")))
    assert tag is not None
    assert [frame.frame_id for frame in tag.frames] == ["TIT2"]
    assert tag.dropped_frames == ("XYZ",)


def test_v22_picture_upgrades_to_apic_with_mime_type() -> None:
    """A v2.2 PIC frame becomes APIC with the image format turned into a MIME type."""
    tag = parse_id3v2(tag_bytes(2, v22_frame("PIC", b"\x00JPG\x03Cover\x00jpegdata")))
    assert tag is not None
    assert tag.frames == (Id3Frame("APIC", b"\x00image/jpeg\x00\x03Cover\x00jpegdata"),)


def test_remove_frames_drops_only_the_named_identifier() -> None:
    """remove_frames removes every frame of one identifier and keeps the others in order."""
    tag = Id3Tag(
        frames=(
            Id3Frame("TKEY", b"\x008A"),
            Id3Frame("TIT2", b"\x00Title"),
            Id3Frame("TKEY", b"\x009A"),
        )
    )
    assert remove_frames(tag, "TKEY").frames == (Id3Frame("TIT2", b"\x00Title"),)


def test_undefined_text_encoding_byte_raises_format_error() -> None:
    """A text frame whose encoding byte is outside 0 to 3 raises Id3FormatError."""
    with pytest.raises(Id3FormatError):
        decode_text_values(Id3Frame("TIT2", b"\x07Title"))
