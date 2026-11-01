"""Locate, read, and replace the ID3v2 tag in MP3, RIFF WAVE, and AIFF bytes.

MP3 files carry the tag at the start of the file. WAV and AIFF files carry it in an ``id3 `` or
``ID3 `` chunk. Every byte outside the tag keeps its value, so audio frames, sample data, and
other chunks survive a rewrite. A trailing ID3v1 tag in an MP3 also stays.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

from infiniteprep.adapters.metadata_io.id3v2 import (
    HEADER_SIZE,
    ID3_MAGIC,
    MAX_PADDING_BYTES,
    Id3Tag,
    parse_id3v2,
    render_id3v23,
    tag_size_from_header,
)

CHUNK_ID_SIZE: Final[int] = 4
CHUNK_HEADER_SIZE: Final[int] = 8
FORM_HEADER_SIZE: Final[int] = 12
# Byte ranges of the form header: magic, then the size field, then the form type.
FORM_SIZE_FIELD: Final[slice] = slice(CHUNK_ID_SIZE, CHUNK_HEADER_SIZE)
FORM_TYPE_FIELD: Final[slice] = slice(CHUNK_HEADER_SIZE, FORM_HEADER_SIZE)
MAX_CHUNK_SIZE: Final[int] = 0xFFFFFFFF
ID3_CHUNK_IDS: Final[frozenset[bytes]] = frozenset({b"id3 ", b"ID3 "})


class AudioContainerError(ValueError):
    """Raised when MP3, WAV, or AIFF bytes do not follow their container layout."""


@dataclass(frozen=True)
class _Container:
    """The header magic, form types, size byte order, and new tag chunk ID of one format."""

    name: str
    magic: bytes
    form_types: frozenset[bytes]
    size_format: str
    new_chunk_id: bytes


@dataclass(frozen=True)
class _Chunk:
    """One chunk: its ID, its byte range including the pad byte, and its body."""

    chunk_id: bytes
    start: int
    end: int
    body: bytes


@dataclass(frozen=True)
class _FormLayout:
    """The chunks of a RIFF or FORM file, plus bytes the chunk walk did not cover."""

    chunks: tuple[_Chunk, ...]
    inner_tail: bytes
    outer_tail: bytes


# RIFF sizes are little endian. IFF sizes are big endian. Each format has a common tag chunk ID.
WAV: Final[_Container] = _Container("WAV", b"RIFF", frozenset({b"WAVE"}), "<I", b"id3 ")
AIFF: Final[_Container] = _Container("AIFF", b"FORM", frozenset({b"AIFF", b"AIFC"}), ">I", b"ID3 ")


def mp3_audio_offset(data: bytes) -> int:
    """Return the offset where MP3 audio starts, after every leading ID3v2 tag."""
    offset = 0
    while data[offset : offset + len(ID3_MAGIC)] == ID3_MAGIC:
        offset += tag_size_from_header(data[offset : offset + HEADER_SIZE])
    if offset > len(data):
        raise AudioContainerError("The leading ID3v2 tag runs past the end; the MP3 is truncated.")
    return offset


def read_mp3_tag(data: bytes) -> Id3Tag | None:
    """Return the leading ID3v2 tag of MP3 data, or None when the file has none."""
    return parse_id3v2(data)


def write_mp3_tag(data: bytes, tag: Id3Tag) -> bytes:
    """Return MP3 data with every leading ID3v2 tag replaced by tag, rendered as v2.3."""
    offset = mp3_audio_offset(data)
    return _render_to_fit(tag, offset) + data[offset:]


def read_wav_tag(data: bytes) -> Id3Tag | None:
    """Return the ID3v2 tag in the first id3 chunk of WAV data, or None."""
    return _read_chunk_tag(data, WAV)


def write_wav_tag(data: bytes, tag: Id3Tag) -> bytes:
    """Return WAV data with its id3 chunk replaced by tag, or with a new chunk appended."""
    return _write_chunk_tag(data, tag, WAV)


def read_aiff_tag(data: bytes) -> Id3Tag | None:
    """Return the ID3v2 tag in the first ID3 chunk of AIFF or AIFC data, or None."""
    return _read_chunk_tag(data, AIFF)


def write_aiff_tag(data: bytes, tag: Id3Tag) -> bytes:
    """Return AIFF or AIFC data with its ID3 chunk replaced by tag, or with a new one appended."""
    return _write_chunk_tag(data, tag, AIFF)


def _render_to_fit(tag: Id3Tag, old_length: int) -> bytes:
    """Render tag padded to old_length when it fits, so the bytes after it do not move."""
    spare = old_length - len(render_id3v23(tag, padding=0))
    if 0 <= spare <= MAX_PADDING_BYTES:
        return render_id3v23(tag, padding=spare)
    return render_id3v23(tag)


def _read_chunk_tag(data: bytes, container: _Container) -> Id3Tag | None:
    """Parse the first tag chunk of a RIFF or FORM file."""
    for chunk in _parse_form(data, container).chunks:
        if chunk.chunk_id in ID3_CHUNK_IDS:
            return parse_id3v2(chunk.body)
    return None


def _write_chunk_tag(data: bytes, tag: Id3Tag, container: _Container) -> bytes:
    """Rebuild a RIFF or FORM file with one tag chunk where the first one was, or at the end."""
    layout = _parse_form(data, container)
    pieces: list[bytes] = []
    replaced = False
    for chunk in layout.chunks:
        if chunk.chunk_id not in ID3_CHUNK_IDS:
            pieces.append(_aligned(data[chunk.start : chunk.end]))
        elif not replaced:
            rendered = _render_to_fit(tag, len(chunk.body))
            pieces.append(_build_chunk(chunk.chunk_id, rendered, container))
            replaced = True
    if not replaced:
        rendered = render_id3v23(tag, padding=0)
        pieces.append(_build_chunk(container.new_chunk_id, rendered, container))
    body = data[FORM_TYPE_FIELD] + b"".join(pieces) + layout.inner_tail
    if len(body) > MAX_CHUNK_SIZE:
        raise AudioContainerError(
            f"The {container.name} file would exceed 4 GiB; remove large frames."
        )
    size = struct.pack(container.size_format, len(body))
    return data[: len(container.magic)] + size + body + layout.outer_tail


def _parse_form(data: bytes, container: _Container) -> _FormLayout:
    """Walk the chunks of a RIFF or FORM file, raising AudioContainerError on damage."""
    end = _form_end(data, container)
    chunks: list[_Chunk] = []
    offset = FORM_HEADER_SIZE
    while offset + CHUNK_HEADER_SIZE <= end:
        chunk = _read_chunk(data, offset, end, container)
        chunks.append(chunk)
        offset = chunk.end
    return _FormLayout(tuple(chunks), data[offset:end], data[end:])


def _form_end(data: bytes, container: _Container) -> int:
    """Check the form header and return where its declared size ends, within the data."""
    magic_ok = data[: len(container.magic)] == container.magic
    form_type = data[FORM_TYPE_FIELD]
    if len(data) < FORM_HEADER_SIZE or not magic_ok or form_type not in container.form_types:
        raise AudioContainerError(f"Data is not a {container.name} file; check the file type.")
    declared = struct.unpack(container.size_format, data[FORM_SIZE_FIELD])[0]
    return min(CHUNK_HEADER_SIZE + declared, len(data))


def _read_chunk(data: bytes, offset: int, end: int, container: _Container) -> _Chunk:
    """Read the chunk at offset, raising AudioContainerError when its body passes end."""
    chunk_id = data[offset : offset + CHUNK_ID_SIZE]
    size_field = data[offset + CHUNK_ID_SIZE : offset + CHUNK_HEADER_SIZE]
    size = struct.unpack(container.size_format, size_field)[0]
    body_start = offset + CHUNK_HEADER_SIZE
    if body_start + size > end:
        chunk_name = chunk_id.decode("latin-1")
        raise AudioContainerError(
            f"The {chunk_name!r} chunk runs past the end; the {container.name} file is damaged."
        )
    # A final chunk may omit its pad byte, so the chunk end never passes the form end.
    chunk_end = min(body_start + size + size % 2, end)
    return _Chunk(chunk_id, offset, chunk_end, data[body_start : body_start + size])


def _build_chunk(chunk_id: bytes, body: bytes, container: _Container) -> bytes:
    """Build one chunk with the container's size byte order and a pad byte for odd sizes."""
    return chunk_id + struct.pack(container.size_format, len(body)) + _aligned(body)


def _aligned(piece: bytes) -> bytes:
    """Append a zero pad byte when piece has an odd length, keeping chunks on even offsets."""
    return piece + b"\x00" if len(piece) % 2 else piece
