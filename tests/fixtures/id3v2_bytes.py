"""Byte builders for ID3v2 test tags, written from the id3v2.3.0 and id3v2.4.0 layouts."""

from __future__ import annotations

import struct

UTF16_BOM_LE = b"\xff\xfe"
SYNC_BYTE = 0xFF
FALSE_SYNC_START = 0xE0
DEFAULT_PADDING = 16


def synchsafe(value: int) -> bytes:
    """Encode value as a 4-byte synchsafe integer."""
    return bytes((value >> shift) & 0x7F for shift in (21, 14, 7, 0))


def utf16(text: str) -> bytes:
    """Encode text as BOM-prefixed little-endian UTF-16."""
    return UTF16_BOM_LE + text.encode("utf-16-le")


def v22_frame(frame_id: str, body: bytes) -> bytes:
    """Build one ID3v2.2 frame with a 3-byte size."""
    return frame_id.encode("ascii") + len(body).to_bytes(3, "big") + body


def v23_frame(frame_id: str, body: bytes, flags: bytes = b"\x00\x00") -> bytes:
    """Build one ID3v2.3 frame with a plain big-endian size."""
    return frame_id.encode("ascii") + struct.pack(">I", len(body)) + flags + body


def v24_frame(frame_id: str, body: bytes, flags: bytes = b"\x00\x00") -> bytes:
    """Build one ID3v2.4 frame with a synchsafe size."""
    return frame_id.encode("ascii") + synchsafe(len(body)) + flags + body


def tag_bytes(major: int, frames: bytes, flags: int = 0, padding: int = DEFAULT_PADDING) -> bytes:
    """Wrap frames in an ID3v2 header of the given major version."""
    body = frames + b"\x00" * padding
    return b"ID3" + bytes((major, 0, flags)) + synchsafe(len(body)) + body


def unsynchronise(data: bytes) -> bytes:
    """Apply the id3v2.3.0 section 5 scheme as a writer would.

    A writer inserts 0x00 after each 0xFF that a byte of 0xE0 or more, a 0x00, or the end
    of the data follows.
    """
    output = bytearray()
    for index, byte in enumerate(data):
        output.append(byte)
        if byte != SYNC_BYTE:
            continue
        following = data[index + 1] if index + 1 < len(data) else None
        if following is None or following == 0 or following >= FALSE_SYNC_START:
            output.append(0)
    return bytes(output)
