"""Behavioral tests for reading and writing Vorbis comments in FLAC metadata blocks."""

from __future__ import annotations

import struct

import pytest

from infiniteprep.adapters.metadata_io.flac_vorbis import (
    FlacFormatError,
    VorbisComment,
    flac_audio_offset,
    get_field_values,
    parse_vorbis_comment,
    read_flac_comment,
    render_vorbis_comment,
    replace_field,
    write_flac_comment,
)
from tests.fixtures.flac_bytes import (
    APPLICATION,
    FLAC_AUDIO,
    PADDING,
    PICTURE,
    SEEKTABLE,
    STREAMINFO,
    VORBIS_COMMENT,
    block_headers,
    flac_block,
    flac_file,
    vorbis_body,
)
from tests.fixtures.id3v2_bytes import tag_bytes, v23_frame

DEFAULT_PADDING = 1024
OLD_COMMENT = vorbis_body("reference libFLAC 1.4.3", "TITLE=Old", "ARTIST=Someone")


def test_file_without_comment_block_reads_none() -> None:
    """A FLAC with only STREAMINFO and padding has no Vorbis comment."""
    data = flac_file(flac_block(PADDING, bytes(64), last=True))
    assert read_flac_comment(data) is None


def test_comment_reads_vendor_and_fields_in_order() -> None:
    """Fields keep their written order, key case, and repeated values."""
    body = vorbis_body("vendor 1", "TITLE=Song", "Genre=House", "GENRE=Techno")
    comment = read_flac_comment(flac_file(flac_block(VORBIS_COMMENT, body, last=True)))
    assert comment == VorbisComment(
        "vendor 1", (("TITLE", "Song"), ("Genre", "House"), ("GENRE", "Techno"))
    )
    assert get_field_values(comment, "genre") == ["House", "Techno"]


def test_entry_without_separator_is_reported_not_kept() -> None:
    """An entry with no '=' is listed in dropped_entries instead of becoming a field."""
    comment = parse_vorbis_comment(vorbis_body("v", "TITLE=Song", "BROKEN"))
    assert comment.fields == (("TITLE", "Song"),)
    assert comment.dropped_entries == ("BROKEN",)


def test_render_round_trips_utf8_values() -> None:
    """Rendering and parsing again keeps UTF-8 text and the vendor string."""
    comment = VorbisComment("vendor", (("ARTIST", "Ché & 東京"), ("BPM", "124")))
    assert parse_vorbis_comment(render_vorbis_comment(comment)) == comment


def test_smaller_comment_fills_padding_so_audio_does_not_move() -> None:
    """When the new comment fits in the old comment plus padding, the file size stays."""
    source = flac_file(
        flac_block(VORBIS_COMMENT, OLD_COMMENT),
        flac_block(PADDING, bytes(256), last=True),
    )
    written = write_flac_comment(source, replace_field(read_flac_comment(source), "TITLE", ["New"]))
    assert len(written) == len(source)
    assert flac_audio_offset(written) == flac_audio_offset(source)
    assert written.endswith(FLAC_AUDIO)
    assert get_field_values(read_flac_comment(written), "TITLE") == ["New"]


def test_comment_larger_than_padding_moves_audio_intact() -> None:
    """A comment that outgrows the space gets default padding, and the audio stays intact."""
    source = flac_file(flac_block(VORBIS_COMMENT, OLD_COMMENT, last=True))
    long_title = "A much longer title " * 30
    written = write_flac_comment(source, VorbisComment("v", (("TITLE", long_title),)))
    assert written[flac_audio_offset(written) :] == FLAC_AUDIO
    assert block_headers(written)[-1] == (PADDING, True, DEFAULT_PADDING)
    assert get_field_values(read_flac_comment(written), "TITLE") == [long_title]


def test_exact_fit_writes_no_padding_block() -> None:
    """A same-size comment with no padding leaves no padding block and the same size."""
    source = flac_file(flac_block(VORBIS_COMMENT, OLD_COMMENT, last=True))
    same_size = replace_field(read_flac_comment(source), "TITLE", ["Odd"])
    written = write_flac_comment(source, same_size)
    assert len(written) == len(source)
    assert [header[0] for header in block_headers(written)] == [STREAMINFO, VORBIS_COMMENT]


def test_other_blocks_keep_their_bytes_and_order() -> None:
    """APPLICATION, SEEKTABLE, and PICTURE blocks stay byte for byte, in order."""
    others = [
        flac_block(APPLICATION, b"riff" + bytes(12)),
        flac_block(SEEKTABLE, bytes(18)),
        flac_block(PICTURE, b"\x00\x00\x00\x03" + bytes(40)),
    ]
    source = flac_file(*others, flac_block(VORBIS_COMMENT, OLD_COMMENT, last=True))
    written = write_flac_comment(source, VorbisComment("v", (("TITLE", "New"),)))
    for block in others:
        assert block in written
    types = [header[0] for header in block_headers(written)]
    assert types[:4] == [STREAMINFO, APPLICATION, SEEKTABLE, PICTURE]


def test_missing_comment_block_is_added_after_streaminfo() -> None:
    """A FLAC without a Vorbis comment gains one right after STREAMINFO."""
    source = flac_file(flac_block(SEEKTABLE, bytes(18), last=True))
    written = write_flac_comment(source, VorbisComment("v", (("TITLE", "New"),)))
    types = [header[0] for header in block_headers(written)]
    assert types[:3] == [STREAMINFO, VORBIS_COMMENT, SEEKTABLE]
    assert written.endswith(FLAC_AUDIO)


def test_duplicate_comment_blocks_become_one() -> None:
    """Only one Vorbis comment block remains, at the first one's position."""
    source = flac_file(
        flac_block(VORBIS_COMMENT, OLD_COMMENT),
        flac_block(SEEKTABLE, bytes(18)),
        flac_block(VORBIS_COMMENT, OLD_COMMENT, last=True),
    )
    written = write_flac_comment(source, VorbisComment("v", (("TITLE", "New"),)))
    types = [header[0] for header in block_headers(written)]
    assert types.count(VORBIS_COMMENT) == 1
    assert types[:3] == [STREAMINFO, VORBIS_COMMENT, SEEKTABLE]


def test_only_the_final_block_carries_the_last_flag() -> None:
    """Every rewritten block except the final one has its last-block flag clear."""
    source = flac_file(
        flac_block(VORBIS_COMMENT, OLD_COMMENT),
        flac_block(PADDING, bytes(8), last=True),
    )
    written = write_flac_comment(source, VorbisComment("v", (("TITLE", "New"),)))
    flags = [header[1] for header in block_headers(written)]
    assert flags == [False] * (len(flags) - 1) + [True]


def test_leading_id3v2_tag_stays_in_front() -> None:
    """An ID3v2 tag before the fLaC magic stays byte for byte."""
    id3 = tag_bytes(3, v23_frame("TIT2", b"\x00Id3"))
    source = flac_file(flac_block(VORBIS_COMMENT, OLD_COMMENT, last=True), prefix=id3)
    written = write_flac_comment(source, VorbisComment("v", (("TITLE", "New"),)))
    assert written.startswith(id3 + b"fLaC")
    assert written.endswith(FLAC_AUDIO)


@pytest.mark.parametrize(
    "data",
    [
        b"OggS" + bytes(60),
        b"fLaC" + flac_block(VORBIS_COMMENT, OLD_COMMENT, last=True),
        b"fLaC" + bytes((STREAMINFO | 0x80,)) + (500).to_bytes(3, "big") + bytes(10),
        b"fLaC" + flac_block(STREAMINFO, bytes(34)),
    ],
)
def test_damaged_or_foreign_data_raises(data: bytes) -> None:
    """Non-FLAC data, a missing STREAMINFO, an overrunning block, or no last block raises."""
    with pytest.raises(FlacFormatError):
        read_flac_comment(data)


def test_entry_count_larger_than_the_body_raises() -> None:
    """A declared entry count that the body cannot hold raises before any entry is read."""
    body = struct.pack("<I", 1) + b"v" + struct.pack("<I", 0xFFFFFFFF)
    with pytest.raises(FlacFormatError):
        parse_vorbis_comment(body)


@pytest.mark.parametrize("key", ["", "A=B", "TÍTLE", "TAB\x7f"])
def test_invalid_field_names_raise(key: str) -> None:
    """Field names must be printable ASCII 0x20 to 0x7D without '='."""
    with pytest.raises(ValueError, match="field name"):
        replace_field(VorbisComment(), key, ["x"])


def test_replace_field_matches_keys_case_insensitively() -> None:
    """Replacing a key removes every case variant and appends the new values."""
    comment = VorbisComment("v", (("Genre", "House"), ("TITLE", "Song"), ("GENRE", "Techno")))
    replaced = replace_field(comment, "GENRE", ["Deep", "Minimal"])
    assert replaced.fields == (("TITLE", "Song"), ("GENRE", "Deep"), ("GENRE", "Minimal"))


def test_flac_format_error_is_a_value_error() -> None:
    """Callers that catch ValueError also catch FLAC damage."""
    assert issubclass(FlacFormatError, ValueError)
