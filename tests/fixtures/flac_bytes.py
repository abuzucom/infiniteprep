"""Byte builders for FLAC test files, written from the FLAC metadata block layout."""

from __future__ import annotations

import struct

STREAMINFO, PADDING, APPLICATION, SEEKTABLE, VORBIS_COMMENT, PICTURE = 0, 1, 2, 3, 4, 6
LAST_BLOCK_FLAG = 0x80
STREAMINFO_BODY = bytes(range(34))
# Stand-in frame bytes. The code under test never decodes audio, so these only need to
# survive a metadata rewrite unchanged.
FLAC_AUDIO = b"\xff\xf8\x69\x08" + bytes(range(256)) * 3


def flac_block(block_type: int, body: bytes, *, last: bool = False) -> bytes:
    """Build one metadata block with a 1-byte type header and a 24-bit big-endian length."""
    header = block_type | (LAST_BLOCK_FLAG if last else 0)
    return bytes((header,)) + len(body).to_bytes(3, "big") + body


def vorbis_body(vendor: str, *entries: str) -> bytes:
    """Build a VORBIS_COMMENT block body from a vendor string and KEY=value entries."""
    parts = [struct.pack("<I", len(vendor.encode())) + vendor.encode()]
    parts.append(struct.pack("<I", len(entries)))
    for entry in entries:
        parts.append(struct.pack("<I", len(entry.encode())) + entry.encode())
    return b"".join(parts)


def flac_file(*blocks: bytes, audio: bytes = FLAC_AUDIO, prefix: bytes = b"") -> bytes:
    """Join prefix, the fLaC magic, STREAMINFO, the given blocks, and audio frames.

    The caller marks the final block as last.
    """
    streaminfo = flac_block(STREAMINFO, STREAMINFO_BODY, last=not blocks)
    return prefix + b"fLaC" + streaminfo + b"".join(blocks) + audio


def block_headers(data: bytes) -> list[tuple[int, bool, int]]:
    """List (type, last, length) for each metadata block after the fLaC magic."""
    offset = data.index(b"fLaC") + 4
    headers: list[tuple[int, bool, int]] = []
    while True:
        length = int.from_bytes(data[offset + 1 : offset + 4], "big")
        last = bool(data[offset] & LAST_BLOCK_FLAG)
        headers.append((data[offset] & 0x7F, last, length))
        offset += 4 + length
        if last:
            return headers
