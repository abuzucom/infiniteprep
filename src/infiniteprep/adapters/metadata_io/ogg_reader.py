"""Read metadata, stream parameters, and Vorbis comments from Ogg audio files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
from typing import Final

from infiniteprep.adapters.metadata_io.flac_vorbis import (
    FlacFormatError,
    VorbisComment,
    parse_vorbis_comment,
)

OGG_MAGIC: Final[bytes] = b"OggS"
HEADER_SIZE: Final[int] = 27
MAX_PAGE_COUNT: Final[int] = 65536
MAX_PACKET_SIZE: Final[int] = 16 * 1024 * 1024
VORBIS_ID_MAGIC: Final[bytes] = b"\x01vorbis"
VORBIS_COMMENT_MAGIC: Final[bytes] = b"\x03vorbis"
OPUS_HEAD_MAGIC: Final[bytes] = b"OpusHead"
OPUS_TAGS_MAGIC: Final[bytes] = b"OpusTags"
OPUS_CLOCK_RATE: Final[int] = 48000


class OggFormatError(ValueError):
    """Raised when data does not conform to the Ogg specification."""


@dataclass(frozen=True)
class OggMetadata:
    """Metadata read from an Ogg Vorbis or Ogg Opus bitstream."""

    codec: str
    channels: int
    sample_rate: int
    duration: float
    bitrate: int
    comment: VorbisComment

    @property
    def vorbis_comment(self) -> VorbisComment:
        """Alias for comment attribute."""
        return self.comment


def _read_page_header(
    data: bytes, offset: int
) -> tuple[int, int, int, int, int]:
    """Read essential header fields and offset past the 27-byte header."""
    if offset + HEADER_SIZE > len(data):
        raise OggFormatError("Truncated Ogg page header.")
    if data[offset:offset + 4] != OGG_MAGIC:
        raise OggFormatError("Expected OggS capture pattern.")
    version = data[offset + 4]
    if version != 0:
        raise OggFormatError(f"Unsupported Ogg version {version}; expected 0.")
    header_type = data[offset + 5]
    granule = struct.unpack_from("<q", data, offset + 6)[0]
    serial = struct.unpack_from("<I", data, offset + 14)[0]
    num_segments = data[offset + 26]
    return (
        header_type,
        granule,
        serial,
        num_segments,
        offset + HEADER_SIZE,
    )


def _parse_page(
    data: bytes, offset: int
) -> tuple[int, int, int, list[int], bytes, int]:
    """Parse one Ogg page at offset into header info, lacing, body, and next offset."""
    flags, granule, serial, num_segs, seg_offset = _read_page_header(data, offset)
    if seg_offset + num_segs > len(data):
        raise OggFormatError("Truncated Ogg segment table.")
    lacing = list(data[seg_offset:seg_offset + num_segs])
    body_start = seg_offset + num_segs
    body_end = body_start + sum(lacing)
    if body_end > len(data):
        raise OggFormatError("Truncated Ogg page body.")
    return flags, granule, serial, lacing, data[body_start:body_end], body_end


def _extract_page_packets(
    lacing: list[int],
    body: bytes,
    pending: list[bytes],
    packets: list[bytes],
) -> None:
    """Consume lacing entries and body slices, appending completed packets."""
    body_pos = 0
    for seg_len in lacing:
        pending.append(body[body_pos:body_pos + seg_len])
        body_pos += seg_len
        current_size = sum(len(chunk) for chunk in pending)
        if current_size > MAX_PACKET_SIZE:
            raise OggFormatError(f"Packet size exceeds {MAX_PACKET_SIZE} bytes.")
        if seg_len < 255:
            packets.append(b"".join(pending))
            pending.clear()


def _validate_continuation(flags: int, has_pending: bool) -> None:
    """Raise OggFormatError if the continuation flag does not match pending state."""
    is_continued = bool(flags & 0x01)
    if is_continued and not has_pending:
        raise OggFormatError("Continuation flag set without pending packet fragment.")


def _assemble_stream_headers(data: bytes) -> tuple[int, list[bytes]]:
    """Assemble the first two packets for the first logical stream."""
    offset = 0
    page_count = 0
    target_serial = -1
    packets: list[bytes] = []
    pending: list[bytes] = []
    while offset < len(data) and len(packets) < 2:
        if page_count >= MAX_PAGE_COUNT:
            raise OggFormatError(f"Page count exceeded maximum limit {MAX_PAGE_COUNT}.")
        page_info = _parse_page(data, offset)
        offset = page_info[5]
        page_count += 1
        serial = page_info[2]
        if target_serial == -1:
            target_serial = serial
        if serial != target_serial:
            continue
        _validate_continuation(page_info[0], bool(pending))
        _extract_page_packets(page_info[3], page_info[4], pending, packets)
    if len(packets) < 2:
        raise OggFormatError("Stream ended before header packets could be assembled.")
    return target_serial, packets


def _check_page_for_granule(data: bytes, pos: int, target_serial: int) -> int | None:
    """Validate page header at pos and return granule if it belongs to target_serial."""
    if pos + HEADER_SIZE > len(data):
        return None
    if data[pos + 4] != 0:
        return None
    serial = struct.unpack_from("<I", data, pos + 14)[0]
    if serial != target_serial:
        return None
    num_segs = data[pos + 26]
    seg_end = pos + HEADER_SIZE + num_segs
    if seg_end > len(data):
        return None
    body_size = sum(data[pos + HEADER_SIZE:seg_end])
    if seg_end + body_size > len(data):
        return None
    granule = struct.unpack_from("<q", data, pos + 6)[0]
    return granule if granule >= 0 else None


def _find_last_granule(data: bytes, target_serial: int) -> int:
    """Scan backwards from file end to locate the last page granule of the stream."""
    search_end = len(data)
    while search_end >= HEADER_SIZE:
        pos = data.rfind(OGG_MAGIC, 0, search_end)
        if pos == -1:
            break
        granule = _check_page_for_granule(data, pos, target_serial)
        if granule is not None:
            return granule
        search_end = pos
    raise OggFormatError("Could not find the last page of the logical stream.")


def _parse_vorbis_id(packet: bytes) -> tuple[int, int, int]:
    """Parse channels, sample rate, and nominal bitrate from a Vorbis ID packet."""
    if len(packet) < 30 or not packet.startswith(VORBIS_ID_MAGIC):
        raise OggFormatError("Invalid Vorbis identification header packet.")
    version = struct.unpack_from("<I", packet, 7)[0]
    if version != 0:
        raise OggFormatError(f"Unsupported Vorbis version {version}; expected 0.")
    channels = packet[11]
    if channels == 0:
        raise OggFormatError("Vorbis channels must be greater than zero.")
    sample_rate = struct.unpack_from("<I", packet, 12)[0]
    if sample_rate == 0:
        raise OggFormatError("Vorbis sample rate must be greater than zero.")
    nominal_bitrate = struct.unpack_from("<i", packet, 20)[0]
    return channels, sample_rate, nominal_bitrate


def _parse_opus_head(packet: bytes) -> tuple[int, int, int]:
    """Parse channels, pre-skip, and input sample rate from an OpusHead packet."""
    if len(packet) < 19 or not packet.startswith(OPUS_HEAD_MAGIC):
        raise OggFormatError("Invalid OpusHead identification header packet.")
    channels = packet[9]
    if channels == 0:
        raise OggFormatError("Opus channels must be greater than zero.")
    pre_skip = struct.unpack_from("<H", packet, 10)[0]
    input_rate = struct.unpack_from("<I", packet, 12)[0]
    sample_rate = input_rate if input_rate > 0 else OPUS_CLOCK_RATE
    return channels, pre_skip, sample_rate


def _parse_comment_packet(codec: str, packet: bytes) -> VorbisComment:
    """Parse the comment packet payload using parse_vorbis_comment."""
    if codec == "vorbis":
        if not packet.startswith(VORBIS_COMMENT_MAGIC):
            raise OggFormatError("Invalid Vorbis comment packet magic.")
        body = packet[len(VORBIS_COMMENT_MAGIC):]
    else:
        if not packet.startswith(OPUS_TAGS_MAGIC):
            raise OggFormatError("Invalid OpusTags packet magic.")
        body = packet[len(OPUS_TAGS_MAGIC):]
    try:
        return parse_vorbis_comment(body)
    except (FlacFormatError, ValueError) as exc:
        raise OggFormatError(f"Damaged Vorbis comment data: {exc}") from exc


def _calculate_duration(
    codec: str, granule: int, sample_rate: int, pre_skip: int
) -> float:
    """Calculate stream duration in seconds from final granule position."""
    if codec == "vorbis":
        return granule / sample_rate
    granule_diff = granule - pre_skip
    if granule_diff <= 0:
        return 0.0
    return granule_diff / OPUS_CLOCK_RATE


def _calculate_bitrate(nominal_bitrate: int, duration: float, file_size: int) -> int:
    """Determine bitrate from nominal bitrate or overall file size and duration."""
    if nominal_bitrate > 0:
        return nominal_bitrate
    if duration > 0:
        return int(round((file_size * 8) / duration))
    return 0


def _parse_codec_info(packet: bytes) -> tuple[str, int, int, int, int]:
    """Identify codec and extract channels, sample rate, nominal bitrate, pre-skip."""
    if packet.startswith(VORBIS_ID_MAGIC):
        channels, rate, nominal = _parse_vorbis_id(packet)
        return "vorbis", channels, rate, nominal, 0
    if packet.startswith(OPUS_HEAD_MAGIC):
        channels, pre_skip, rate = _parse_opus_head(packet)
        return "opus", channels, rate, 0, pre_skip
    raise OggFormatError("First logical stream is neither Vorbis nor Opus.")


def read_ogg(source: bytes | Path | str) -> OggMetadata:
    """Read metadata, codec parameters, and comments from Ogg Vorbis or Opus data."""
    raw = Path(source).read_bytes() if isinstance(source, (str, Path)) else source
    if not isinstance(raw, bytes):
        raise TypeError("Ogg source must be bytes, Path, or str.")
    serial, packets = _assemble_stream_headers(raw)
    info = _parse_codec_info(packets[0])
    comment = _parse_comment_packet(info[0], packets[1])
    granule = _find_last_granule(raw, serial)
    duration = _calculate_duration(info[0], granule, info[2], info[4])
    bitrate = _calculate_bitrate(info[3], duration, len(raw))
    return OggMetadata(info[0], info[1], info[2], duration, bitrate, comment)


def read_ogg_metadata(source: bytes | Path | str) -> OggMetadata:
    """Read metadata, codec parameters, and comments from Ogg Vorbis or Opus data."""
    return read_ogg(source)
