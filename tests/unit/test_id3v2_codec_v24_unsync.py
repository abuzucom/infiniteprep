"""Behavioral tests for ID3v2.4 unsynchronisation set in the tag header, a frame, or both."""

from __future__ import annotations

from infiniteprep.adapters.metadata_io.id3v2 import Id3Frame, parse_id3v2
from tests.fixtures.id3v2_bytes import tag_bytes, unsynchronise, v24_frame

TAG_FLAG_UNSYNC = 0x80
FRAME_FLAG_UNSYNC = b"\x00\x02"
# 0xFF before 0xE0 and before 0x00 both gain an inserted 0x00 when unsynchronised.
PRIV_BODY = b"owner\x00\xff\xe0\xff\x00\x00"


def test_v24_tag_header_unsync_applies_to_every_frame() -> None:
    """The v2.4 header flag alone means every frame body is unsynchronised."""
    raw = v24_frame("PRIV", unsynchronise(PRIV_BODY)) + v24_frame("TIT2", b"\x03After\x00")
    tag = parse_id3v2(tag_bytes(4, raw, flags=TAG_FLAG_UNSYNC))
    assert tag is not None
    assert tag.dropped_frames == ()
    assert tag.frames == (Id3Frame("PRIV", PRIV_BODY), Id3Frame("TIT2", b"\x03After\x00"))


def test_v24_header_and_frame_unsync_reverse_once() -> None:
    """With both the header and the frame flag set, the codec reverses the scheme one time."""
    raw = v24_frame("PRIV", unsynchronise(PRIV_BODY), FRAME_FLAG_UNSYNC)
    tag = parse_id3v2(tag_bytes(4, raw, flags=TAG_FLAG_UNSYNC))
    assert tag is not None
    assert tag.frames == (Id3Frame("PRIV", PRIV_BODY),)


def test_v24_frame_unsync_without_header_flag_still_reverses() -> None:
    """A v2.4 frame flag reverses unsynchronisation for that frame when the header flag is clear."""
    raw = v24_frame("PRIV", unsynchronise(PRIV_BODY), FRAME_FLAG_UNSYNC)
    tag = parse_id3v2(tag_bytes(4, raw))
    assert tag is not None
    assert tag.frames == (Id3Frame("PRIV", PRIV_BODY),)
