"""Read and write the Vorbis comment block of FLAC bytes.

The layouts follow the FLAC format specification and the Vorbis comment header. Metadata blocks
other than VORBIS_COMMENT and PADDING keep their bytes and their order. Audio frames and a
leading ID3v2 tag stay byte for byte. Padding absorbs size changes where it can, so the audio
offset stays the same.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from infiniteprep.adapters.metadata_io.id3_containers import mp3_audio_offset

FLAC_MAGIC: Final[bytes] = b"fLaC"
BLOCK_HEADER_SIZE: Final[int] = 4
BLOCK_LENGTH_BYTES: Final[int] = 3
STREAMINFO, PADDING, VORBIS_COMMENT = 0, 1, 4
LAST_BLOCK_FLAG: Final[int] = 0x80
BLOCK_TYPE_MASK: Final[int] = 0x7F
MAX_BLOCK_SIZE: Final[int] = (1 << 24) - 1
DEFAULT_PADDING_BYTES: Final[int] = 1024
LENGTH_FIELD_SIZE: Final[int] = 4
FIELD_SEPARATOR: Final[str] = "="
# Vorbis comment field names use printable ASCII 0x20 through 0x7D, excluding '='.
FIELD_NAME_FIRST: Final[int] = 0x20
FIELD_NAME_LAST: Final[int] = 0x7D


class FlacFormatError(ValueError):
    """Raised when bytes claim to be FLAC or a Vorbis comment but do not follow the format."""


@dataclass(frozen=True)
class VorbisComment:
    """A Vorbis comment as ordered (name, value) fields. dropped_entries lists unusable entries."""

    vendor: str = ""
    fields: tuple[tuple[str, str], ...] = ()
    dropped_entries: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Block:
    """One metadata block: its type and its body."""

    block_type: int
    body: bytes


@dataclass(frozen=True)
class _FlacLayout:
    """Bytes before the first block, the metadata blocks, and the audio frames after them."""

    prefix: bytes
    blocks: tuple[_Block, ...]
    audio: bytes


def parse_vorbis_comment(body: bytes) -> VorbisComment:
    """Parse a VORBIS_COMMENT block body. Entries without '=' go to dropped_entries."""
    vendor, offset = _read_length_prefixed(body, 0)
    count, offset = _read_uint32(body, offset)
    if count * LENGTH_FIELD_SIZE > len(body) - offset:
        raise FlacFormatError(f"Vorbis comment claims {count} entries; the block is damaged.")
    fields: list[tuple[str, str]] = []
    dropped: list[str] = []
    for _ in range(count):
        raw, offset = _read_length_prefixed(body, offset)
        name, separator, value = raw.decode("utf-8", errors="replace").partition(FIELD_SEPARATOR)
        if separator:
            fields.append((name, value))
        else:
            dropped.append(name)
    vendor_text = vendor.decode("utf-8", errors="replace")
    return VorbisComment(vendor_text, tuple(fields), tuple(dropped))


def render_vorbis_comment(comment: VorbisComment) -> bytes:
    """Render comment as a VORBIS_COMMENT block body, without the Ogg framing bit."""
    parts = [_length_prefixed(comment.vendor.encode("utf-8"))]
    parts.append(struct.pack("<I", len(comment.fields)))
    for name, value in comment.fields:
        _validate_field_name(name)
        parts.append(_length_prefixed(f"{name}{FIELD_SEPARATOR}{value}".encode()))
    return b"".join(parts)


def get_field_values(comment: VorbisComment, name: str) -> list[str]:
    """Return the values of every field whose name matches name, ignoring case."""
    wanted = name.upper()
    return [value for field_name, value in comment.fields if field_name.upper() == wanted]


def replace_field(comment: VorbisComment, name: str, values: Sequence[str]) -> VorbisComment:
    """Return comment with every case variant of name removed and values appended under name."""
    _validate_field_name(name)
    wanted = name.upper()
    kept = tuple(field for field in comment.fields if field[0].upper() != wanted)
    return replace(comment, fields=kept + tuple((name, value) for value in values))


def read_flac_comment(data: bytes) -> VorbisComment | None:
    """Return the first Vorbis comment of FLAC data, or None when the file has none."""
    for block in _parse_flac(data).blocks:
        if block.block_type == VORBIS_COMMENT:
            return parse_vorbis_comment(block.body)
    return None


def write_flac_comment(data: bytes, comment: VorbisComment) -> bytes:
    """Return FLAC data with its Vorbis comment replaced by comment."""
    layout = _parse_flac(data)
    body = render_vorbis_comment(comment)
    if len(body) > MAX_BLOCK_SIZE:
        raise FlacFormatError("The Vorbis comment exceeds 16 MiB; remove large fields.")
    blocks = _place_comment(layout.blocks, body)
    spare = _metadata_size(layout.blocks) - _metadata_size(blocks)
    padding = _padding_length(spare)
    if padding is not None:
        blocks = (*blocks, _Block(PADDING, bytes(padding)))
    return layout.prefix + _render_blocks(blocks) + layout.audio


def flac_audio_offset(data: bytes) -> int:
    """Return the offset where FLAC audio frames start, after every metadata block."""
    return len(data) - len(_parse_flac(data).audio)


def _parse_flac(data: bytes) -> _FlacLayout:
    """Split FLAC data into its prefix, metadata blocks, and audio, raising on damage."""
    start = mp3_audio_offset(data)
    if data[start : start + len(FLAC_MAGIC)] != FLAC_MAGIC:
        raise FlacFormatError("Data is not a FLAC file; expected the fLaC marker.")
    offset = start + len(FLAC_MAGIC)
    blocks: list[_Block] = []
    is_last = False
    while not is_last:
        block, is_last = _read_block(data, offset)
        blocks.append(block)
        offset += BLOCK_HEADER_SIZE + len(block.body)
    if blocks[0].block_type != STREAMINFO:
        raise FlacFormatError(
            "The first FLAC metadata block is not STREAMINFO; the file is damaged."
        )
    return _FlacLayout(data[: start + len(FLAC_MAGIC)], tuple(blocks), data[offset:])


def _read_block(data: bytes, offset: int) -> tuple[_Block, bool]:
    """Read the metadata block at offset and whether it carries the last-block flag."""
    header = data[offset : offset + BLOCK_HEADER_SIZE]
    if len(header) < BLOCK_HEADER_SIZE:
        raise FlacFormatError("FLAC metadata ends without a last block; the file is truncated.")
    length = int.from_bytes(header[1:], "big")
    body_start = offset + BLOCK_HEADER_SIZE
    if body_start + length > len(data):
        raise FlacFormatError("A FLAC metadata block runs past the end; the file is truncated.")
    block = _Block(header[0] & BLOCK_TYPE_MASK, data[body_start : body_start + length])
    return block, bool(header[0] & LAST_BLOCK_FLAG)


def _place_comment(blocks: tuple[_Block, ...], body: bytes) -> tuple[_Block, ...]:
    """Drop padding, keep one comment where the first was, or add one after STREAMINFO."""
    result: list[_Block] = []
    placed = False
    for block in blocks:
        if block.block_type == PADDING:
            continue
        if block.block_type != VORBIS_COMMENT:
            result.append(block)
        elif not placed:
            result.append(_Block(VORBIS_COMMENT, body))
            placed = True
    if not placed:
        result.insert(1, _Block(VORBIS_COMMENT, body))
    return tuple(result)


def _padding_length(spare: int) -> int | None:
    """Return the padding body length that keeps the old size, or None for no padding block.

    A spare of 1 to 3 bytes cannot hold a block header, so it falls back to default padding.
    """
    if spare == 0:
        return None
    if BLOCK_HEADER_SIZE <= spare <= MAX_BLOCK_SIZE + BLOCK_HEADER_SIZE:
        return spare - BLOCK_HEADER_SIZE
    return DEFAULT_PADDING_BYTES


def _metadata_size(blocks: tuple[_Block, ...]) -> int:
    """Return the byte length of blocks with their headers."""
    return sum(BLOCK_HEADER_SIZE + len(block.body) for block in blocks)


def _render_blocks(blocks: tuple[_Block, ...]) -> bytes:
    """Render blocks with 24-bit lengths, setting the last-block flag on the final one only."""
    parts: list[bytes] = []
    for index, block in enumerate(blocks):
        flag = LAST_BLOCK_FLAG if index == len(blocks) - 1 else 0
        length = len(block.body).to_bytes(BLOCK_LENGTH_BYTES, "big")
        parts.append(bytes((block.block_type | flag,)) + length + block.body)
    return b"".join(parts)


def _validate_field_name(name: str) -> None:
    """Raise ValueError unless name is a valid Vorbis comment field name."""
    valid = bool(name) and all(
        FIELD_NAME_FIRST <= ord(char) <= FIELD_NAME_LAST and char != FIELD_SEPARATOR
        for char in name
    )
    if not valid:
        raise ValueError(
            f"Vorbis comment field name {name!r} is invalid; use printable ASCII without '='."
        )


def _read_uint32(body: bytes, offset: int) -> tuple[int, int]:
    """Read a little-endian 32-bit integer at offset and return it with the next offset."""
    end = offset + LENGTH_FIELD_SIZE
    if end > len(body):
        raise FlacFormatError(
            "The Vorbis comment ends inside a length field; the block is damaged."
        )
    return struct.unpack("<I", body[offset:end])[0], end


def _read_length_prefixed(body: bytes, offset: int) -> tuple[bytes, int]:
    """Read a 32-bit length and that many bytes, returning them with the next offset."""
    length, start = _read_uint32(body, offset)
    if start + length > len(body):
        raise FlacFormatError("A Vorbis comment string runs past the block; the block is damaged.")
    return body[start : start + length], start + length


def _length_prefixed(raw: bytes) -> bytes:
    """Prefix raw with its little-endian 32-bit length."""
    return struct.pack("<I", len(raw)) + raw
