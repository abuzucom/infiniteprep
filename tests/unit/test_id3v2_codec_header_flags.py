"""Behavioral tests for ID3v2.2 header flags and the ID3v2.4 footer."""

from __future__ import annotations

from infiniteprep.adapters.metadata_io.id3v2 import Id3Frame, parse_id3v2
from tests.fixtures.id3v2_bytes import synchsafe, tag_bytes, unsynchronise, v22_frame, v24_frame

# id3v2-00 section 3.1: bit 7 is unsynchronisation and bit 6 is compression.
V22_FLAG_UNSYNC = 0x80
V22_FLAG_COMPRESSION = 0x40
# id3v2.4.0 section 3.1: bit 4 marks a footer after the frames.
V24_FLAG_FOOTER = 0x10
TITLE_BODY = b"\x00Title \xff\xe0"


def test_v22_compression_bit_drops_the_whole_tag() -> None:
    """Bit 6 of a v2.2 header marks compression, so the codec keeps no frames and says why."""
    tag = parse_id3v2(tag_bytes(2, v22_frame("TT2", TITLE_BODY), flags=V22_FLAG_COMPRESSION))
    assert tag is not None
    assert tag.frames == ()
    assert tag.dropped_frames == ("compressed ID3v2.2 tag",)


def test_v22_unsync_bit_alone_keeps_frames() -> None:
    """Bit 7 of a v2.2 header is unsynchronisation, which the codec reverses and keeps."""
    frames = unsynchronise(v22_frame("TT2", TITLE_BODY))
    tag = parse_id3v2(tag_bytes(2, frames, flags=V22_FLAG_UNSYNC))
    assert tag is not None
    assert tag.dropped_frames == ()
    assert tag.frames == (Id3Frame("TIT2", TITLE_BODY),)


def test_v24_footer_stays_outside_the_frame_area() -> None:
    """The v2.4 header size excludes the footer, so every frame parses and nothing drops."""
    frames = v24_frame("TIT2", b"\x03Title\x00") + v24_frame("TPE1", b"\x03Artist\x00")
    footer = b"3DI\x04\x00" + bytes((V24_FLAG_FOOTER,)) + synchsafe(len(frames))
    tag = parse_id3v2(tag_bytes(4, frames, flags=V24_FLAG_FOOTER, padding=0) + footer)
    assert tag is not None
    assert tag.dropped_frames == ()
    assert tag.frames == (
        Id3Frame("TIT2", b"\x03Title\x00"),
        Id3Frame("TPE1", b"\x03Artist\x00"),
    )
