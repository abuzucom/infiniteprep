"""Synthetic Ogg Vorbis and Ogg Opus byte generators for testing."""

from __future__ import annotations

import struct
from typing import Final

OGG_MAGIC: Final[bytes] = b"OggS"
VORBIS_ID_MAGIC: Final[bytes] = b"\x01vorbis"
VORBIS_COMMENT_MAGIC: Final[bytes] = b"\x03vorbis"
OPUS_HEAD_MAGIC: Final[bytes] = b"OpusHead"
OPUS_TAGS_MAGIC: Final[bytes] = b"OpusTags"


def _compute_lacing(packets: list[bytes]) -> bytes:
    """Compute Ogg lacing table bytes for a list of packets."""
    table: list[int] = []
    for pkt in packets:
        table.extend([255] * (len(pkt) // 255) + [len(pkt) % 255])
    return bytes(table)


def make_ogg_page(
    serial: int,
    sequence_number: int,
    granule_position: int,
    packets: list[bytes],
    header_type: int = 0,
    version: int = 0,
) -> bytes:
    """Construct one Ogg page from packets, computing the lacing table."""
    lacing = _compute_lacing(packets)
    header = struct.pack(
        "<4sBBqIIIB",
        OGG_MAGIC,
        version,
        header_type,
        granule_position,
        serial,
        sequence_number,
        0,  # CRC dummy
        len(lacing),
    )
    return header + lacing + b"".join(packets)


def make_vorbis_id_header(
    channels: int = 2,
    sample_rate: int = 44100,
    nominal_bitrate: int = 160000,
) -> bytes:
    """Construct a 30-byte Vorbis identification header packet."""
    return struct.pack(
        "<7sIBIiiiBB",
        VORBIS_ID_MAGIC,
        0,  # version
        channels,
        sample_rate,
        0,  # max bitrate
        nominal_bitrate,
        0,  # min bitrate
        0xB8,  # blocksize_0 and blocksize_1
        1,  # framing flag
    )


def make_vorbis_comment_payload(
    vendor: str = "TestVendor",
    fields: list[tuple[str, str]] | None = None,
) -> bytes:
    """Construct a Vorbis comment payload without magic."""
    entries = fields or []
    vendor_bytes = vendor.encode("utf-8")
    parts = [struct.pack("<I", len(vendor_bytes)), vendor_bytes]
    parts.append(struct.pack("<I", len(entries)))
    for name, value in entries:
        raw_field = f"{name}={value}".encode("utf-8")
        parts.append(struct.pack("<I", len(raw_field)))
        parts.append(raw_field)
    return b"".join(parts)


def make_vorbis_comment_header(
    vendor: str = "TestVendor",
    fields: list[tuple[str, str]] | None = None,
) -> bytes:
    """Construct a Vorbis comment header packet with magic and framing bit."""
    payload = make_vorbis_comment_payload(vendor, fields)
    return VORBIS_COMMENT_MAGIC + payload + b"\x01"


def make_opus_head_header(
    channels: int = 2,
    pre_skip: int = 312,
    input_rate: int = 48000,
) -> bytes:
    """Construct a 19-byte OpusHead identification header packet."""
    return struct.pack(
        "<8sBBHIhB",
        OPUS_HEAD_MAGIC,
        1,  # version
        channels,
        pre_skip,
        input_rate,
        0,  # output gain
        0,  # channel mapping family
    )


def make_opus_tags_header(
    vendor: str = "OpusVendor",
    fields: list[tuple[str, str]] | None = None,
) -> bytes:
    """Construct an OpusTags header packet with magic."""
    payload = make_vorbis_comment_payload(vendor, fields)
    return OPUS_TAGS_MAGIC + payload


def make_synthetic_vorbis_ogg(
    channels: int = 2,
    sample_rate: int = 44100,
    nominal_bitrate: int = 160000,
    granule: int = 88200,
    vendor: str = "TestVendor",
    fields: list[tuple[str, str]] | None = None,
    serial: int = 12345,
) -> bytes:
    """Construct complete synthetic Ogg Vorbis bytes."""
    return (
        make_ogg_page(
            serial,
            0,
            0,
            [make_vorbis_id_header(channels, sample_rate, nominal_bitrate)],
            header_type=0x02,
        )
        + make_ogg_page(serial, 1, 0, [make_vorbis_comment_header(vendor, fields)])
        + make_ogg_page(serial, 2, granule, [b"\x00\x01\x02\x03" * 64], header_type=0x04)
    )


def make_synthetic_opus_ogg(
    channels: int = 2,
    pre_skip: int = 312,
    input_rate: int = 48000,
    granule: int = 96312,
    vendor: str = "OpusVendor",
    fields: list[tuple[str, str]] | None = None,
    serial: int = 67890,
) -> bytes:
    """Construct complete synthetic Ogg Opus bytes."""
    return (
        make_ogg_page(
            serial,
            0,
            0,
            [make_opus_head_header(channels, pre_skip, input_rate)],
            header_type=0x02,
        )
        + make_ogg_page(serial, 1, 0, [make_opus_tags_header(vendor, fields)])
        + make_ogg_page(serial, 2, granule, [b"\xfa\xfb\xfc\xfd" * 64], header_type=0x04)
    )
