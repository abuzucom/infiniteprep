"""Behavioral tests for ID3v2.4 timestamp conversion when the codec renders ID3v2.3."""

from __future__ import annotations

from infiniteprep.adapters.metadata_io.id3v2 import (
    decode_text_values,
    parse_id3v2,
    render_id3v23,
)


def synchsafe(value: int) -> bytes:
    """Encode value as a 4-byte synchsafe integer."""
    return bytes((value >> shift) & 0x7F for shift in (21, 14, 7, 0))


def v24_utf8_frame(frame_id: str, text: str) -> bytes:
    """Build one ID3v2.4 UTF-8 text frame with a synchsafe size."""
    body = b"\x03" + text.encode("utf-8") + b"\x00"
    return frame_id.encode("ascii") + synchsafe(len(body)) + b"\x00\x00" + body


def rendered_frames(frames: bytes) -> dict[str, list[str]]:
    """Wrap frames in a v2.4 tag, render it as v2.3, and map identifiers to their values."""
    body = frames + b"\x00" * 16
    source = parse_id3v2(b"ID3\x04\x00\x00" + synchsafe(len(body)) + body)
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
