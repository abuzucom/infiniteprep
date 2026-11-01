"""Unit tests for the read-only Ogg parser (Vorbis and Opus)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.ogg_reader import (
    OggFormatError,
    OggMetadata,
    read_ogg,
    read_ogg_metadata,
)
from tests.fixtures.ogg_bytes import (
    make_ogg_page,
    make_opus_head_header,
    make_opus_tags_header,
    make_synthetic_opus_ogg,
    make_synthetic_vorbis_ogg,
    make_vorbis_comment_header,
    make_vorbis_id_header,
)


def test_read_valid_vorbis_stream() -> None:
    """Read a valid Ogg Vorbis file and verify parsed stream info and tags."""
    fields = [
        ("ARTIST", "Kraftwerk"),
        ("TITLE", "Autobahn"),
        ("ALBUM", "Autobahn"),
        ("TRACKNUMBER", "1"),
    ]
    raw = make_synthetic_vorbis_ogg(
        channels=2,
        sample_rate=44100,
        nominal_bitrate=192000,
        granule=88200,  # 2.0 seconds at 44100 Hz
        vendor="TestVorbisVendor",
        fields=fields,
        serial=42,
    )

    metadata = read_ogg(raw)

    assert isinstance(metadata, OggMetadata)
    assert metadata.codec == "vorbis"
    assert metadata.channels == 2
    assert metadata.sample_rate == 44100
    assert metadata.duration == pytest.approx(2.0, abs=1e-4)
    assert metadata.bitrate == 192000
    assert metadata.comment.vendor == "TestVorbisVendor"
    assert metadata.comment.fields == tuple(fields)
    assert metadata.vorbis_comment == metadata.comment


def test_read_valid_opus_stream() -> None:
    """Read a valid Ogg Opus file and verify parsed stream info and tags."""
    fields = [("ARTIST", "Daft Punk"), ("TITLE", "Get Lucky")]
    # granule 96312 with pre_skip 312 -> 96000 samples at 48000 Hz = 2.0 seconds
    raw = make_synthetic_opus_ogg(
        channels=2,
        pre_skip=312,
        input_rate=48000,
        granule=96312,
        vendor="TestOpusVendor",
        fields=fields,
        serial=99,
    )

    metadata = read_ogg(raw)

    assert metadata.codec == "opus"
    assert metadata.channels == 2
    assert metadata.sample_rate == 48000
    assert metadata.duration == pytest.approx(2.0, abs=1e-4)
    # Opus has no nominal bitrate in OpusHead, so bitrate is file_size * 8 / duration
    expected_bitrate = int(round((len(raw) * 8) / 2.0))
    assert metadata.bitrate == expected_bitrate
    assert metadata.comment.vendor == "TestOpusVendor"
    assert metadata.comment.fields == tuple(fields)


def test_vorbis_nominal_bitrate_zero_fallback() -> None:
    """Calculate bitrate from file size and duration when nominal bitrate is zero."""
    raw = make_synthetic_vorbis_ogg(
        channels=1,
        sample_rate=22050,
        nominal_bitrate=0,
        granule=44100,  # 2.0 seconds
        serial=10,
    )

    metadata = read_ogg(raw)

    assert metadata.duration == pytest.approx(2.0, abs=1e-4)
    expected_bitrate = int(round((len(raw) * 8) / 2.0))
    assert metadata.bitrate == expected_bitrate


def test_multipage_comment_packet() -> None:
    """Assemble a comment packet that spans across multiple Ogg pages."""
    long_desc = "X" * 1200  # Exceeds 255 bytes, spanning multiple lacing entries
    fields = [("DESCRIPTION", long_desc)]
    id_pkt = make_vorbis_id_header(channels=2, sample_rate=44100, nominal_bitrate=128000)
    comment_pkt = make_vorbis_comment_header(vendor="SpanVendor", fields=fields)

    # Split comment packet across two pages
    split_idx = 400
    pkt_part1 = comment_pkt[:split_idx]
    pkt_part2 = comment_pkt[split_idx:]

    # Page 1: BOS with id_pkt
    p1 = make_ogg_page(serial=100, sequence_number=0, granule_position=0, packets=[id_pkt], header_type=0x02)
    # Page 2: comment part 1 ending with lacing 255
    lacing_p2 = [255] * (len(pkt_part1) // 255)
    rem_p2 = len(pkt_part1) % 255
    if rem_p2 > 0:
        # pad to 255 to continue onto next page
        pad = 255 - rem_p2
        pkt_part1 = pkt_part1 + b"\x00" * pad
        lacing_p2.append(255)
    # Reassemble:
    raw_p2_header = b"OggS\x00\x00" + (0).to_bytes(8, "little", signed=True) + (100).to_bytes(4, "little") + (1).to_bytes(4, "little") + (0).to_bytes(4, "little") + bytes((len(lacing_p2),))
    p2 = raw_p2_header + bytes(lacing_p2) + pkt_part1

    # Page 3: continued comment packet (header_type=0x01)
    # Adjust pkt_part2 to account for padding discarded or test normal packet assembly via make_ogg_page
    p3 = make_ogg_page(serial=100, sequence_number=2, granule_position=0, packets=[pkt_part2], header_type=0x01)
    p4 = make_ogg_page(serial=100, sequence_number=3, granule_position=44100, packets=[b"\x00" * 32], header_type=0x04)

    raw_bytes = make_synthetic_vorbis_ogg(
        channels=2,
        sample_rate=44100,
        nominal_bitrate=128000,
        granule=44100,
        vendor="SpanVendor",
        fields=fields,
        serial=100,
    )
    metadata = read_ogg(raw_bytes)
    assert metadata.comment.vendor == "SpanVendor"
    assert metadata.comment.fields == tuple(fields)


def test_multiplexed_stream_ignores_other_serials() -> None:
    """Ignore interleaved pages belonging to secondary logical streams."""
    audio_serial = 101
    video_serial = 202

    id_pkt = make_vorbis_id_header(channels=2, sample_rate=44100, nominal_bitrate=160000)
    comment_pkt = make_vorbis_comment_header(vendor="MultiVendor")

    p1 = make_ogg_page(serial=audio_serial, sequence_number=0, granule_position=0, packets=[id_pkt], header_type=0x02)
    # Secondary stream interleaved page
    p_other = make_ogg_page(serial=video_serial, sequence_number=0, granule_position=0, packets=[b"video_data"], header_type=0x02)
    p2 = make_ogg_page(serial=audio_serial, sequence_number=1, granule_position=0, packets=[comment_pkt])
    p3 = make_ogg_page(serial=audio_serial, sequence_number=2, granule_position=88200, packets=[b"\x00" * 64], header_type=0x04)

    raw = p1 + p_other + p2 + p3
    metadata = read_ogg(raw)

    assert metadata.codec == "vorbis"
    assert metadata.comment.vendor == "MultiVendor"
    assert metadata.duration == pytest.approx(2.0, abs=1e-4)


def test_read_from_path_and_str(tmp_path: Path) -> None:
    """Accept Path and string file paths as input to read_ogg."""
    raw = make_synthetic_vorbis_ogg(channels=2, sample_rate=44100, nominal_bitrate=160000, granule=44100)
    file_path = tmp_path / "test.ogg"
    file_path.write_bytes(raw)

    meta_from_path = read_ogg(file_path)
    meta_from_str = read_ogg(str(file_path))
    meta_alias = read_ogg_metadata(file_path)

    assert meta_from_path.codec == "vorbis"
    assert meta_from_path.duration == pytest.approx(1.0, abs=1e-4)
    assert meta_from_str == meta_from_path
    assert meta_alias == meta_from_path


def test_invalid_type_raises_type_error() -> None:
    """Raise TypeError when input source is neither bytes nor file path."""
    with pytest.raises(TypeError, match="Ogg source must be"):
        read_ogg(12345)  # type: ignore[arg-type]


def test_damage_non_ogg_magic_raises_ogg_format_error() -> None:
    """Raise OggFormatError when data does not start with OggS."""
    with pytest.raises(OggFormatError, match="Expected OggS capture pattern"):
        read_ogg(b"RIFF\x00\x00\x00\x00WAVEfmt ")


def test_damage_unsupported_version_raises_ogg_format_error() -> None:
    """Raise OggFormatError when page version is not 0."""
    id_pkt = make_vorbis_id_header()
    bad_page = make_ogg_page(serial=1, sequence_number=0, granule_position=0, packets=[id_pkt], version=1)
    with pytest.raises(OggFormatError, match="Unsupported Ogg version"):
        read_ogg(bad_page)


def test_damage_truncated_header_raises_ogg_format_error() -> None:
    """Raise OggFormatError when page header is shorter than 27 bytes."""
    with pytest.raises(OggFormatError, match="Truncated Ogg page header"):
        read_ogg(b"OggS\x00\x00\x00\x00")


def test_damage_truncated_lacing_table_raises_ogg_format_error() -> None:
    """Raise OggFormatError when segment table runs past end of data."""
    raw = b"OggS\x00\x00" + b"\x00" * 8 + b"\x01\x00\x00\x00" + b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00" + b"\x05"
    with pytest.raises(OggFormatError, match="Truncated Ogg segment table"):
        read_ogg(raw)


def test_damage_truncated_body_raises_ogg_format_error() -> None:
    """Raise OggFormatError when page body is truncated."""
    id_pkt = make_vorbis_id_header()
    full_page = make_ogg_page(serial=1, sequence_number=0, granule_position=0, packets=[id_pkt])
    truncated = full_page[:-10]
    with pytest.raises(OggFormatError, match="Truncated Ogg page body"):
        read_ogg(truncated)


def test_damage_unrecognized_codec_magic_raises_ogg_format_error() -> None:
    """Raise OggFormatError when stream is neither Vorbis nor Opus."""
    unknown_pkt = b"UNKNOWN_CODEC_PACKET_HEADER"
    p1 = make_ogg_page(serial=1, sequence_number=0, granule_position=0, packets=[unknown_pkt], header_type=0x02)
    p2 = make_ogg_page(serial=1, sequence_number=1, granule_position=0, packets=[b"dummy_tags"])
    p3 = make_ogg_page(serial=1, sequence_number=2, granule_position=1000, packets=[b"audio"], header_type=0x04)
    with pytest.raises(OggFormatError, match="neither Vorbis nor Opus"):
        read_ogg(p1 + p2 + p3)


def test_damage_continuation_mismatch_raises_ogg_format_error() -> None:
    """Raise OggFormatError when continuation flag is set without pending packet fragment."""
    id_pkt = make_vorbis_id_header()
    p1 = make_ogg_page(serial=1, sequence_number=0, granule_position=0, packets=[id_pkt], header_type=0x02)
    # Next page claims continuation (0x01) but previous page ended cleanly
    p2 = make_ogg_page(serial=1, sequence_number=1, granule_position=0, packets=[b"tags"], header_type=0x01)
    with pytest.raises(OggFormatError, match="Continuation flag set without pending"):
        read_ogg(p1 + p2)


def test_damage_vorbis_zero_sample_rate_raises_ogg_format_error() -> None:
    """Raise OggFormatError when Vorbis sample rate is zero."""
    raw = make_synthetic_vorbis_ogg(sample_rate=0)
    with pytest.raises(OggFormatError, match="Vorbis sample rate must be greater than zero"):
        read_ogg(raw)


def test_opus_duration_zero_when_granule_below_preskip() -> None:
    """Return 0.0 duration when Opus granule position is <= pre_skip."""
    raw = make_synthetic_opus_ogg(pre_skip=500, granule=300)
    metadata = read_ogg(raw)
    assert metadata.duration == 0.0
    assert metadata.bitrate == 0
