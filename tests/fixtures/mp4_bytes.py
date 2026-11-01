"""Byte builders for MP4 and M4A test files, written from the ISO base media file layout."""

from __future__ import annotations

import struct

FIXED_16_16_SHIFT = 16


def atom(atom_type: bytes, body: bytes = b"") -> bytes:
    """Build one atom with a 32-bit size."""
    return struct.pack(">I", 8 + len(body)) + atom_type + body


def large_atom(atom_type: bytes, body: bytes) -> bytes:
    """Build one atom with size 1 and a 64-bit largesize."""
    return struct.pack(">I", 1) + atom_type + struct.pack(">Q", 16 + len(body)) + body


def full_atom(atom_type: bytes, body: bytes, version: int = 0) -> bytes:
    """Build a full atom whose body starts with a version byte and three flag bytes."""
    return atom(atom_type, bytes((version, 0, 0, 0)) + body)


def mvhd(timescale: int, duration: int, version: int = 0) -> bytes:
    """Build an mvhd atom holding timescale and duration, padded like a real header."""
    if version == 1:
        times = struct.pack(">QQIQ", 0, 0, timescale, duration)
    else:
        times = struct.pack(">IIII", 0, 0, timescale, duration)
    return full_atom(b"mvhd", times + bytes(80), version)


def hdlr(handler: bytes) -> bytes:
    """Build an hdlr atom naming handler, such as soun or vide."""
    return full_atom(b"hdlr", bytes(4) + handler + bytes(12) + b"\x00")


def audio_entry(
    entry_type: bytes, channels: int, sample_rate: int, *children: bytes, sample_size: int = 16
) -> bytes:
    """Build an audio sample entry with a 16.16 sample rate and child atoms."""
    rate = (sample_rate << FIXED_16_16_SHIFT) & 0xFFFFFFFF
    fields = bytes(6) + struct.pack(">H", 1) + bytes(8)
    fields += struct.pack(">HHHHI", channels, sample_size, 0, 0, rate)
    return atom(entry_type, fields + b"".join(children))


def esds(avg_bitrate: int) -> bytes:
    """Build an esds atom with an ES descriptor holding a decoder config."""
    config = bytes((0x40, 0x15)) + bytes(3) + struct.pack(">II", avg_bitrate, avg_bitrate)
    decoder = bytes((0x04, len(config))) + config
    es_body = struct.pack(">H", 1) + b"\x00" + decoder
    return full_atom(b"esds", bytes((0x03, len(es_body))) + es_body)


def alac_config(sample_rate: int, channels: int, bit_depth: int, avg_bitrate: int) -> bytes:
    """Build the alac configuration atom nested in an alac sample entry."""
    body = struct.pack(">IBBBBBBHII", 4096, 0, bit_depth, 40, 10, 14, channels, 255, 0, avg_bitrate)
    return full_atom(b"alac", body + struct.pack(">I", sample_rate))


def stsz(sample_sizes: list[int]) -> bytes:
    """Build an stsz atom with a per-sample size table."""
    table = b"".join(struct.pack(">I", size) for size in sample_sizes)
    return full_atom(b"stsz", struct.pack(">II", 0, len(sample_sizes)) + table)


def trak(handler: bytes, entry: bytes = b"", sizes: list[int] | None = None) -> bytes:
    """Build a trak holding an hdlr and, for a sample entry, an stbl with stsd and stsz."""
    stbl_children = b""
    if entry:
        stbl_children = full_atom(b"stsd", struct.pack(">I", 1) + entry)
        stbl_children += stsz(sizes or [])
    minf = atom(b"minf", atom(b"stbl", stbl_children))
    return atom(b"trak", atom(b"mdia", hdlr(handler) + minf))


def data_atom(type_code: int, payload: bytes) -> bytes:
    """Build an ilst data atom with a type indicator and an empty locale."""
    return atom(b"data", struct.pack(">II", type_code, 0) + payload)


def item(key: bytes, *datas: bytes) -> bytes:
    """Build one ilst item atom holding data atoms."""
    return atom(key, b"".join(datas))


def freeform(mean: str, name: str, value: str) -> bytes:
    """Build a ---- freeform item with mean, name, and one UTF-8 value."""
    mean_atom = full_atom(b"mean", mean.encode())
    name_atom = full_atom(b"name", name.encode())
    return atom(b"----", mean_atom + name_atom + data_atom(1, value.encode()))


def udta(*items: bytes, full_meta: bool = True) -> bytes:
    """Build udta.meta.ilst holding items. full_meta=False omits the meta version and flags."""
    children = hdlr(b"mdir") + atom(b"ilst", b"".join(items))
    meta = full_atom(b"meta", children) if full_meta else atom(b"meta", children)
    return atom(b"udta", meta)


def m4a_file(*moov_children: bytes, mdat: bytes = b"\x00" * 64) -> bytes:
    """Join ftyp, a moov holding moov_children, and an mdat."""
    ftyp = atom(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A isom")
    return ftyp + atom(b"moov", b"".join(moov_children)) + atom(b"mdat", mdat)
