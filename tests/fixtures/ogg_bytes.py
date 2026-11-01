"""Synthetic Ogg Vorbis and Ogg Opus byte generators for testing."""

from __future__ import annotations

import struct
from typing import Final

OGG_MAGIC: Final[bytes] = b"OggS"
VORBIS_ID_MAGIC: Final[bytes] = b"\x01vorbis"
VORBIS_COMMENT_MAGIC: Final[bytes] = b"\x03vorbis"
OPUS_HEAD_MAGIC: Final[bytes] = b"OpusHead"
OPUS_TAGS_MAGIC: Final[bytes] = b"OpusTags"


def make_ogg_page(
    serial: int,
    sequence_number: int,
    granule_position: int,
    packets: list[bytes],
    header_type: int = 0,
    version: int = 0,
) -> bytes:
    """Construct one Ogg page from packets, computing the lacing table."""
    lacing: list[int] = []
    body_parts: list[bytes] = []
    for pkt in packets:
        body_parts.append(pkt)
        remaining = len(pkt)
        while remaining >= 255:
            lacing.append(255)
            remaining -= 255
        lacing.append(remaining)

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
    return header + bytes(lacing) + b"".join(body_parts)


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
    id_pkt = make_vorbis_id_header(channels, sample_rate, nominal_bitrate)
    comment_pkt = make_vorbis_comment_header(vendor, fields)
    page_bos = make_ogg_page(serial, 0, 0, [id_pkt], header_type=0x02)
    page_tags = make_ogg_page(serial, 1, 0, [comment_pkt])
    dummy_audio = b"\x00\x01\x02\x03" * 64
    page_eos = make_ogg_page(serial, 2, granule, [dummy_audio], header_type=0x04)
    return page_bos + page_tags + page_eos


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
    id_pkt = make_opus_head_header(channels, pre_skip, input_rate)
    tags_pkt = make_opus_tags_header(vendor, fields)
    page_bos = make_ogg_page(serial, 0, 0, [id_pkt], header_type=0x02)
    page_tags = make_ogg_page(serial, 1, 0, [tags_pkt])
    dummy_audio = b"\xfa\xfb\xfc\xfd" * 64
    page_eos = make_ogg_page(serial, 2, granule, [dummy_audio], header_type=0x04)
    return page_bos + page_tags + page_eos
