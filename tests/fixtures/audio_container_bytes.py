"""Byte builders for MP3, RIFF WAVE, and AIFF test files, written from the container layouts."""

from __future__ import annotations

import struct

# Two MPEG-1 Layer III frame headers with zeroed side data stand in for audio. The codec under
# test never decodes audio, so the bytes only need to survive a tag rewrite unchanged.
MP3_AUDIO = (b"\xff\xfb\x90\x00" + bytes(413)) * 2
ID3V1_TAG = b"TAG" + b"Title".ljust(30, b"\x00") + bytes(94) + b"\xff"
PCM_SAMPLES = bytes(range(256)) * 4
# fmt chunk body for 2-channel, 44.1 kHz, 16-bit PCM.
WAV_FMT_BODY = struct.pack("<HHIIHH", 1, 2, 44100, 44100 * 4, 4, 16)
# COMM chunk body for 2-channel, 1024 frames, 16-bit, with an 80-bit 44.1 kHz rate.
AIFF_COMM_BODY = struct.pack(">hIh", 2, 1024, 16) + b"\x40\x0e\xac\x44" + bytes(6)


def riff_chunk(chunk_id: bytes, body: bytes) -> bytes:
    """Build one little-endian RIFF chunk with its pad byte."""
    return chunk_id + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")


def iff_chunk(chunk_id: bytes, body: bytes) -> bytes:
    """Build one big-endian IFF chunk with its pad byte."""
    return chunk_id + struct.pack(">I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")


def wav_file(*chunks: bytes) -> bytes:
    """Wrap chunks in a RIFF WAVE header."""
    payload = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def aiff_file(*chunks: bytes, form_type: bytes = b"AIFF") -> bytes:
    """Wrap chunks in an IFF FORM header of the given form type."""
    payload = form_type + b"".join(chunks)
    return b"FORM" + struct.pack(">I", len(payload)) + payload


def basic_wav(*extra_chunks: bytes) -> bytes:
    """Build a WAV with fmt and data chunks followed by extra_chunks."""
    fmt = riff_chunk(b"fmt ", WAV_FMT_BODY)
    return wav_file(fmt, riff_chunk(b"data", PCM_SAMPLES), *extra_chunks)


def basic_aiff(*extra_chunks: bytes, form_type: bytes = b"AIFF") -> bytes:
    """Build an AIFF with COMM and SSND chunks followed by extra_chunks."""
    ssnd = iff_chunk(b"SSND", bytes(8) + PCM_SAMPLES)
    return aiff_file(iff_chunk(b"COMM", AIFF_COMM_BODY), ssnd, *extra_chunks, form_type=form_type)
