"""Render ID3v2 tags as v2.3 and read back the text values for the codec tests."""

from __future__ import annotations

from infiniteprep.adapters.metadata_io.id3v2 import decode_text_values, parse_id3v2, render_id3v23
from tests.fixtures.id3v2_bytes import tag_bytes, v24_frame

ID3V24_MAJOR = 4
UTF8_ENCODING = b"\x03"
TEXT_TERMINATOR = b"\x00"
RECORDING_TIME_FRAME = "TDRC"


def render_tag_values(source_bytes: bytes) -> dict[str, list[str]]:
    """Render a tag as v2.3, parse it again, and map each frame identifier to its values."""
    source = parse_id3v2(source_bytes)
    assert source is not None
    reparsed = parse_id3v2(render_id3v23(source))
    assert reparsed is not None
    values: dict[str, list[str]] = {}
    for frame in reparsed.frames:
        values.setdefault(frame.frame_id, []).extend(decode_text_values(frame))
    return values


def render_v24_frames(frames: bytes) -> dict[str, list[str]]:
    """Wrap frames in a v2.4 tag, render it as v2.3, and map identifiers to their values."""
    return render_tag_values(tag_bytes(ID3V24_MAJOR, frames))


def build_v24_utf8_frame(frame_id: str, text: str) -> bytes:
    """Build one ID3v2.4 UTF-8 text frame with a synchsafe size."""
    return v24_frame(frame_id, UTF8_ENCODING + text.encode("utf-8") + TEXT_TERMINATOR)


def render_v24_timestamp(timestamp: str) -> dict[str, list[str]]:
    """Render a v2.4 tag holding one UTF-8 TDRC frame as v2.3 and map identifiers to values."""
    return render_v24_frames(build_v24_utf8_frame(RECORDING_TIME_FRAME, timestamp))
