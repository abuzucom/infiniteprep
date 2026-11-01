"""Behavioral tests for the ID3v2 codec resource limits on decompression and padding."""

from __future__ import annotations

import struct
import zlib

import pytest

from infiniteprep.adapters.metadata_io.id3v2 import (
    MAX_INFLATED_FRAME_BYTES,
    MAX_INFLATED_TAG_BYTES,
    MAX_PADDING_BYTES,
    Id3Frame,
    Id3Tag,
    parse_id3v2,
    render_id3v23,
)
from tests.fixtures.id3v2_bytes import tag_bytes, v23_frame

V23_COMPRESSED = b"\x00\x80"
OWNER_PREFIX = b"owner\x00"


def compressed_priv_frame(inflated_size: int) -> bytes:
    """Build a compressed v2.3 PRIV frame whose body inflates to inflated_size bytes."""
    plain = OWNER_PREFIX + bytes(inflated_size - len(OWNER_PREFIX))
    raw = struct.pack(">I", len(plain)) + zlib.compress(plain)
    return v23_frame("PRIV", raw, V23_COMPRESSED)


def v23_tag(frames: bytes) -> bytes:
    """Wrap frames in an ID3v2.3 header with 16 bytes of padding."""
    return tag_bytes(3, frames)


def test_frame_inflating_past_the_frame_limit_is_dropped() -> None:
    """A compressed frame that inflates past MAX_INFLATED_FRAME_BYTES is dropped and named."""
    tag = parse_id3v2(v23_tag(compressed_priv_frame(MAX_INFLATED_FRAME_BYTES + 1)))
    assert tag is not None
    assert tag.frames == ()
    assert tag.dropped_frames == ("PRIV",)


def test_frame_inflating_to_exactly_the_frame_limit_is_kept() -> None:
    """A compressed frame at MAX_INFLATED_FRAME_BYTES still inflates in full."""
    tag = parse_id3v2(v23_tag(compressed_priv_frame(MAX_INFLATED_FRAME_BYTES)))
    assert tag is not None
    assert tag.dropped_frames == ()
    assert len(tag.frames[0].body) == MAX_INFLATED_FRAME_BYTES


def test_frames_past_the_tag_inflation_budget_are_dropped() -> None:
    """Once a tag inflates MAX_INFLATED_TAG_BYTES in total, later compressed frames are dropped."""
    frames_in_budget = MAX_INFLATED_TAG_BYTES // MAX_INFLATED_FRAME_BYTES
    frame = compressed_priv_frame(MAX_INFLATED_FRAME_BYTES)
    tag = parse_id3v2(v23_tag(frame * (frames_in_budget + 1)))
    assert tag is not None
    assert len(tag.frames) == frames_in_budget
    assert tag.dropped_frames == ("PRIV",)


@pytest.mark.parametrize("padding", [-1, MAX_PADDING_BYTES + 1])
def test_render_rejects_padding_outside_the_limit(padding: int) -> None:
    """Negative padding and padding above MAX_PADDING_BYTES raise ValueError."""
    with pytest.raises(ValueError, match="padding"):
        render_id3v23(Id3Tag(frames=(Id3Frame("TIT2", b"\x00Title"),)), padding=padding)


def test_render_accepts_padding_at_the_limit() -> None:
    """MAX_PADDING_BYTES of padding renders and parses back."""
    rendered = render_id3v23(Id3Tag(frames=(Id3Frame("TIT2", b"\x00Title"),)), MAX_PADDING_BYTES)
    assert rendered.endswith(b"\x00" * MAX_PADDING_BYTES)
    reparsed = parse_id3v2(rendered)
    assert reparsed is not None
    assert reparsed.frames == (Id3Frame("TIT2", b"\x00Title"),)
