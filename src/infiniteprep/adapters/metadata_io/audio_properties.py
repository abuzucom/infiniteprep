"""Read audio stream properties from MP3, RIFF WAVE, AIFF, and FLAC bytes. The module never writes.

The readers walk only headers: MPEG frame headers with Xing, Info, and VBRI, the RIFF and IFF
chunk headers, and the FLAC metadata block headers. They slice out the format chunk and the tag
chunk or Vorbis comment, and never copy the audio. A memory-mapped file therefore stays mostly on
disk while its properties are read.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Final

from infiniteprep.adapters.metadata_io.id3_containers import mp3_audio_offset

BITS_PER_BYTE: Final[int] = 8
UINT32_SIZE: Final[int] = 4
CHUNK_HEADER_SIZE: Final[int] = 8
FORM_HEADER_SIZE: Final[int] = 12
MAX_CHUNKS: Final[int] = 4096
ID3_CHUNK_IDS: Final[frozenset[bytes]] = frozenset({b"id3 ", b"ID3 "})
# A WAV fmt chunk holds format tag, channels, rate, byte rate, block align, and bits per sample.
WAV_FMT_LAYOUT: Final[str] = "<HHIIHH"
WAV_FMT_SIZE: Final[int] = struct.calcsize(WAV_FMT_LAYOUT)
# An AIFF COMM chunk holds channels, frame count, and sample size, then an 80-bit sample rate.
AIFF_COMM_LAYOUT: Final[str] = ">hIh"
AIFF_COMM_FIELDS: Final[int] = struct.calcsize(AIFF_COMM_LAYOUT)
EXTENDED_FLOAT_SIZE: Final[int] = 10
EXTENDED_EXPONENT_BIAS: Final[int] = 16383
EXTENDED_MANTISSA_BITS: Final[int] = 63
EXTENDED_EXPONENT_MASK: Final[int] = 0x7FFF
EXTENDED_SIGN_BIT: Final[int] = 0x8000
# FLAC metadata: a 4-byte magic, then blocks of a 1-byte type and a 24-bit length.
FLAC_MAGIC: Final[bytes] = b"fLaC"
FLAC_BLOCK_HEADER_SIZE: Final[int] = 4
FLAC_LAST_BLOCK: Final[int] = 0x80
FLAC_TYPE_MASK: Final[int] = 0x7F
FLAC_STREAMINFO, FLAC_VORBIS_COMMENT = 0, 4
FLAC_STREAMINFO_SIZE: Final[int] = 34
# STREAMINFO packs rate, channels, bit depth, and sample count into 8 bytes at offset 10.
STREAMINFO_PACKED: Final[slice] = slice(10, 18)
# Within those 8 bytes: a 20-bit rate, 3 bits of channels - 1, 5 bits of depth - 1, and a
# 36-bit sample count.
STREAMINFO_RATE_SHIFT: Final[int] = 44
STREAMINFO_CHANNELS_SHIFT, STREAMINFO_CHANNELS_MASK = 41, 0x7
STREAMINFO_BITS_SHIFT, STREAMINFO_BITS_MASK = 36, 0x1F
STREAMINFO_TOTAL_MASK: Final[int] = (1 << 36) - 1
MAX_FLAC_BLOCKS: Final[int] = 4096
# MPEG audio frame header fields.
MPEG_HEADER_SIZE: Final[int] = 4
MPEG_SYNC_BYTE: Final[int] = 0xFF
MPEG_SYNC_MASK: Final[int] = 0xFFE00000
MAX_SYNC_SEARCH: Final[int] = 1 << 20
MPEG1, MPEG2, MPEG25 = 3, 2, 0
LAYER1, LAYER2, LAYER3 = 3, 2, 1
MONO_MODE: Final[int] = 3
# Bit positions of the header fields, counted from the low end of the 32-bit word.
VERSION_SHIFT, LAYER_SHIFT, BITRATE_SHIFT = 19, 17, 12
RATE_SHIFT, PADDING_SHIFT, MODE_SHIFT = 10, 9, 6
TWO_BIT_MASK, FOUR_BIT_MASK = 0x3, 0xF
# Layer I frames hold 384 samples in 4-byte slots. Layer II and MPEG-1 Layer III frames hold
# 1152 samples. MPEG-2 and MPEG-2.5 Layer III frames hold 576.
LAYER1_SAMPLES, LAYER1_SLOT_FACTOR, LAYER1_SLOT_BYTES = 384, 12, 4
LONG_FRAME_SAMPLES, SHORT_FRAME_SAMPLES = 1152, 576
FREE_BITRATE, BAD_BITRATE, BAD_RATE = 0, 15, 3
ID3V1_SIZE: Final[int] = 128
ID3V1_MAGIC: Final[bytes] = b"TAG"
KBPS: Final[int] = 1000
# Bitrates in kbps by index, for (MPEG-1 or not, layer).
BITRATES: Final[dict[tuple[bool, int], tuple[int, ...]]] = {
    (True, LAYER1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (True, LAYER2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (True, LAYER3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (False, LAYER1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (False, LAYER2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (False, LAYER3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
SAMPLE_RATES: Final[dict[int, tuple[int, int, int]]] = {
    MPEG1: (44100, 48000, 32000),
    MPEG2: (22050, 24000, 16000),
    MPEG25: (11025, 12000, 8000),
}
# Side information length after the frame header, for (MPEG-1 or not, mono or not).
SIDE_INFO_BYTES: Final[dict[tuple[bool, bool], int]] = {
    (True, False): 32,
    (True, True): 17,
    (False, False): 17,
    (False, True): 9,
}
XING_MARKERS: Final[frozenset[bytes]] = frozenset({b"Xing", b"Info"})
XING_FRAMES_FLAG, XING_BYTES_FLAG = 0x1, 0x2
VBRI_MARKER: Final[bytes] = b"VBRI"
VBRI_OFFSET: Final[int] = MPEG_HEADER_SIZE + 32
# VBRI holds version, delay, and quality as 16-bit values, then byte and frame counts.
VBRI_LAYOUT: Final[str] = ">HHHII"


class AudioPropertiesError(ValueError):
    """Raised when audio bytes do not hold a readable stream header."""


@dataclass(frozen=True)
class AudioProperties:
    """Stream properties. A field is None when the file does not state it."""

    duration: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bitrate: int | None = None
    bits_per_sample: int | None = None


@dataclass(frozen=True)
class StreamRead:
    """Properties plus the raw tag: an ID3v2 tag for WAV and AIFF, a Vorbis comment for FLAC."""

    properties: AudioProperties
    tag_body: bytes | None


@dataclass(frozen=True)
class _ChunkSpan:
    """One chunk: its ID, where its body starts, and its body size clamped to the form."""

    chunk_id: bytes
    start: int
    size: int


@dataclass(frozen=True)
class _MpegHeader:
    """The fields of one MPEG audio frame header that timing needs."""

    is_mpeg1: bool
    layer: int
    bitrate: int
    sample_rate: int
    mono: bool
    frame_bytes: int
    samples_per_frame: int


def read_wav_stream(data: bytes) -> StreamRead:
    """Read WAV properties from the fmt and data chunks, plus the first id3 chunk body."""
    chunks = _walk_form(data, b"RIFF", frozenset({b"WAVE"}), "<I")
    fmt = _first_chunk(chunks, frozenset({b"fmt "}))
    if fmt is None or fmt.size < WAV_FMT_SIZE:
        raise AudioPropertiesError("The WAV file has no usable fmt chunk; the file is damaged.")
    fields = struct.unpack(WAV_FMT_LAYOUT, data[fmt.start : fmt.start + WAV_FMT_SIZE])
    _, channels, rate, _, block_align, bits = fields
    audio = _first_chunk(chunks, frozenset({b"data"}))
    frames = audio.size // block_align if audio and block_align else 0
    properties = _pcm_properties(channels, rate, bits, frames)
    return StreamRead(properties, _chunk_body(data, chunks, ID3_CHUNK_IDS))


def read_aiff_stream(data: bytes) -> StreamRead:
    """Read AIFF or AIFC properties from the COMM chunk, plus the first ID3 chunk body."""
    chunks = _walk_form(data, b"FORM", frozenset({b"AIFF", b"AIFC"}), ">I")
    comm = _first_chunk(chunks, frozenset({b"COMM"}))
    if comm is None or comm.size < AIFF_COMM_FIELDS + EXTENDED_FLOAT_SIZE:
        raise AudioPropertiesError("The AIFF file has no usable COMM chunk; the file is damaged.")
    body = data[comm.start : comm.start + AIFF_COMM_FIELDS + EXTENDED_FLOAT_SIZE]
    channels, frames, bits = struct.unpack(AIFF_COMM_LAYOUT, body[:AIFF_COMM_FIELDS])
    rate = _extended_to_int(body[AIFF_COMM_FIELDS:])
    properties = _pcm_properties(channels, rate, bits, frames)
    return StreamRead(properties, _chunk_body(data, chunks, ID3_CHUNK_IDS))


def read_flac_stream(data: bytes) -> StreamRead:
    """Read FLAC properties from STREAMINFO, plus the first Vorbis comment block body."""
    start = mp3_audio_offset(data)
    if data[start : start + len(FLAC_MAGIC)] != FLAC_MAGIC:
        raise AudioPropertiesError("Data is not a FLAC stream; check the file type.")
    blocks, audio_offset = _walk_flac_blocks(data, start + len(FLAC_MAGIC))
    if not blocks or blocks[0][0] != FLAC_STREAMINFO or blocks[0][2] < FLAC_STREAMINFO_SIZE:
        raise AudioPropertiesError("The FLAC stream does not start with STREAMINFO.")
    info_start = blocks[0][1]
    info = data[info_start : info_start + FLAC_STREAMINFO_SIZE]
    properties = _decode_streaminfo(info, len(data) - audio_offset)
    comment = next((block for block in blocks if block[0] == FLAC_VORBIS_COMMENT), None)
    tag_body = data[comment[1] : comment[1] + comment[2]] if comment else None
    return StreamRead(properties, tag_body)


def _decode_streaminfo(info: bytes, audio_bytes: int) -> AudioProperties:
    """Decode rate, channels, bit depth, and sample count from a STREAMINFO body."""
    packed = struct.unpack(">Q", info[STREAMINFO_PACKED])[0]
    rate = packed >> STREAMINFO_RATE_SHIFT
    total = packed & STREAMINFO_TOTAL_MASK
    duration = total / rate if rate else None
    bitrate = int(audio_bytes * BITS_PER_BYTE / duration) if duration else None
    channels = ((packed >> STREAMINFO_CHANNELS_SHIFT) & STREAMINFO_CHANNELS_MASK) + 1
    bits = ((packed >> STREAMINFO_BITS_SHIFT) & STREAMINFO_BITS_MASK) + 1
    return AudioProperties(duration, rate or None, channels, bitrate, bits)


def read_mp3_properties(data: bytes) -> AudioProperties:
    """Read MP3 timing from the first frame chain, using Xing, Info, or VBRI when present."""
    audio_start = mp3_audio_offset(data)
    frame_start, header = _find_first_frame(data, audio_start)
    audio_end = len(data)
    if data[audio_end - ID3V1_SIZE : audio_end - ID3V1_SIZE + len(ID3V1_MAGIC)] == ID3V1_MAGIC:
        audio_end -= ID3V1_SIZE
    vbr = _vbr_counts(data, frame_start, header)
    if vbr is not None and vbr[0]:
        frames, vbr_bytes = vbr
        duration = frames * header.samples_per_frame / header.sample_rate
        stream_bytes = vbr_bytes or (audio_end - frame_start)
        bitrate = int(stream_bytes * BITS_PER_BYTE / duration)
    else:
        duration = (audio_end - frame_start) * BITS_PER_BYTE / header.bitrate
        bitrate = header.bitrate
    channels = 1 if header.mono else 2
    return AudioProperties(duration, header.sample_rate, channels, bitrate, None)


def _pcm_properties(channels: int, rate: int, bits: int, frames: int) -> AudioProperties:
    """Build properties for uncompressed audio from its channels, rate, depth, and frames."""
    duration = frames / rate if rate else None
    bitrate = channels * bits * rate or None
    return AudioProperties(duration, rate or None, channels or None, bitrate, bits or None)


def _walk_form(
    data: bytes, magic: bytes, form_types: frozenset[bytes], size_format: str
) -> list[_ChunkSpan]:
    """List the chunks of a RIFF or IFF form without copying their bodies."""
    if data[: len(magic)] != magic or data[CHUNK_HEADER_SIZE:FORM_HEADER_SIZE] not in form_types:
        raise AudioPropertiesError(f"Data is not a {magic.decode()} audio file; check the type.")
    declared = struct.unpack(size_format, data[UINT32_SIZE:CHUNK_HEADER_SIZE])[0]
    end = min(CHUNK_HEADER_SIZE + declared, len(data))
    chunks: list[_ChunkSpan] = []
    offset = FORM_HEADER_SIZE
    while offset + CHUNK_HEADER_SIZE <= end and len(chunks) < MAX_CHUNKS:
        size_field = data[offset + UINT32_SIZE : offset + CHUNK_HEADER_SIZE]
        size = struct.unpack(size_format, size_field)[0]
        start = offset + CHUNK_HEADER_SIZE
        # A truncated recording can declare more bytes than exist. Count only the real ones.
        chunk_id = data[offset : offset + UINT32_SIZE]
        chunks.append(_ChunkSpan(chunk_id, start, min(size, end - start)))
        offset = start + size + size % 2
    return chunks


def _first_chunk(chunks: list[_ChunkSpan], ids: frozenset[bytes]) -> _ChunkSpan | None:
    """Return the first chunk whose ID is in ids, or None."""
    return next((chunk for chunk in chunks if chunk.chunk_id in ids), None)


def _chunk_body(data: bytes, chunks: list[_ChunkSpan], ids: frozenset[bytes]) -> bytes | None:
    """Return the body of the first chunk whose ID is in ids, or None."""
    chunk = _first_chunk(chunks, ids)
    return data[chunk.start : chunk.start + chunk.size] if chunk else None


def _extended_to_int(raw: bytes) -> int:
    """Convert an 80-bit IEEE 754 extended float, as AIFF stores its rate, to an integer."""
    exponent_field, mantissa = struct.unpack(">HQ", raw[:EXTENDED_FLOAT_SIZE])
    exponent = (exponent_field & EXTENDED_EXPONENT_MASK) - EXTENDED_EXPONENT_BIAS
    if mantissa == 0 or exponent_field & EXTENDED_EXPONENT_MASK == EXTENDED_EXPONENT_MASK:
        return 0
    value = math.ldexp(mantissa, exponent - EXTENDED_MANTISSA_BITS)
    return 0 if exponent_field & EXTENDED_SIGN_BIT else round(value)


def _walk_flac_blocks(data: bytes, offset: int) -> tuple[list[tuple[int, int, int]], int]:
    """List (type, body start, length) for each FLAC metadata block, and the audio offset."""
    blocks: list[tuple[int, int, int]] = []
    while len(blocks) < MAX_FLAC_BLOCKS:
        header = data[offset : offset + FLAC_BLOCK_HEADER_SIZE]
        if len(header) < FLAC_BLOCK_HEADER_SIZE:
            raise AudioPropertiesError("FLAC metadata ends early; the file is truncated.")
        length = int.from_bytes(header[1:], "big")
        start = offset + FLAC_BLOCK_HEADER_SIZE
        if start + length > len(data):
            raise AudioPropertiesError("A FLAC metadata block runs past the end of the file.")
        blocks.append((header[0] & FLAC_TYPE_MASK, start, length))
        offset = start + length
        if header[0] & FLAC_LAST_BLOCK:
            return blocks, offset
    raise AudioPropertiesError("The FLAC file has too many metadata blocks; it is damaged.")


def _find_first_frame(data: bytes, start: int) -> tuple[int, _MpegHeader]:
    """Return the offset and header of the first frame that a second valid frame follows."""
    limit = min(len(data), start + MAX_SYNC_SEARCH)
    position = data.find(bytes((MPEG_SYNC_BYTE,)), start, limit)
    while position != -1:
        header = _parse_mpeg_header(data[position : position + MPEG_HEADER_SIZE])
        if header is not None and _frame_chain_continues(data, position, header):
            return position, header
        position = data.find(bytes((MPEG_SYNC_BYTE,)), position + 1, limit)
    raise AudioPropertiesError("No MPEG audio frame found; the file is not an MP3 or is damaged.")


def _frame_chain_continues(data: bytes, position: int, header: _MpegHeader) -> bool:
    """Return True when the next frame header is valid, or when the data ends there."""
    following = position + header.frame_bytes
    if following + MPEG_HEADER_SIZE > len(data):
        return following <= len(data)
    return _parse_mpeg_header(data[following : following + MPEG_HEADER_SIZE]) is not None


def _parse_mpeg_header(raw: bytes) -> _MpegHeader | None:
    """Decode a 4-byte MPEG audio frame header, or return None when it is not valid."""
    if len(raw) < MPEG_HEADER_SIZE:
        return None
    word = int.from_bytes(raw, "big")
    version = (word >> VERSION_SHIFT) & TWO_BIT_MASK
    layer = (word >> LAYER_SHIFT) & TWO_BIT_MASK
    bitrate_index = (word >> BITRATE_SHIFT) & FOUR_BIT_MASK
    rate_index = (word >> RATE_SHIFT) & TWO_BIT_MASK
    invalid = version == 1 or layer == 0 or rate_index == BAD_RATE
    if word & MPEG_SYNC_MASK != MPEG_SYNC_MASK or invalid:
        return None
    if bitrate_index in (FREE_BITRATE, BAD_BITRATE):
        return None
    is_mpeg1 = version == MPEG1
    bitrate = BITRATES[(is_mpeg1, layer)][bitrate_index] * KBPS
    rate = SAMPLE_RATES[version][rate_index]
    return _frame_geometry(is_mpeg1, layer, bitrate, rate, word)


def _frame_geometry(is_mpeg1: bool, layer: int, bitrate: int, rate: int, word: int) -> _MpegHeader:
    """Compute frame length and samples per frame from the decoded header fields."""
    padding = (word >> PADDING_SHIFT) & 1
    if layer == LAYER1:
        samples = LAYER1_SAMPLES
        frame_bytes = (LAYER1_SLOT_FACTOR * bitrate // rate + padding) * LAYER1_SLOT_BYTES
    else:
        samples = LONG_FRAME_SAMPLES if is_mpeg1 or layer == LAYER2 else SHORT_FRAME_SAMPLES
        frame_bytes = samples // BITS_PER_BYTE * bitrate // rate + padding
    mono = (word >> MODE_SHIFT) & TWO_BIT_MASK == MONO_MODE
    return _MpegHeader(is_mpeg1, layer, bitrate, rate, mono, frame_bytes, samples)


def _vbr_counts(data: bytes, frame_start: int, header: _MpegHeader) -> tuple[int, int] | None:
    """Return (frames, bytes) from a Xing, Info, or VBRI header in the first frame, or None."""
    xing_at = frame_start + MPEG_HEADER_SIZE + SIDE_INFO_BYTES[(header.is_mpeg1, header.mono)]
    if data[xing_at : xing_at + UINT32_SIZE] in XING_MARKERS:
        fields = data[xing_at + UINT32_SIZE : xing_at + 4 * UINT32_SIZE]
        flags = struct.unpack(">I", fields[:UINT32_SIZE])[0] if len(fields) >= UINT32_SIZE else 0
        values = [
            struct.unpack(">I", fields[i : i + UINT32_SIZE])[0]
            for i in range(UINT32_SIZE, len(fields) - UINT32_SIZE + 1, UINT32_SIZE)
        ]
        frames = values.pop(0) if flags & XING_FRAMES_FLAG and values else 0
        stream_bytes = values.pop(0) if flags & XING_BYTES_FLAG and values else 0
        return frames, stream_bytes
    vbri_at = frame_start + VBRI_OFFSET
    if data[vbri_at : vbri_at + UINT32_SIZE] == VBRI_MARKER:
        raw = data[vbri_at + UINT32_SIZE : vbri_at + UINT32_SIZE + struct.calcsize(VBRI_LAYOUT)]
        if len(raw) == struct.calcsize(VBRI_LAYOUT):
            _, _, _, stream_bytes, frames = struct.unpack(VBRI_LAYOUT, raw)
            return frames, stream_bytes
    return None
