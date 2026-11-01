"""Read iTunes-style tags and audio properties from MP4 and M4A bytes. The module never writes.

The layouts follow the ISO base media file format and the iTunes metadata atoms. The reader
walks only the paths it needs: moov.mvhd for duration, the first sound track's sample
description for the codec, rate, and channels, and moov.udta.meta.ilst for tags. It has no
limit on the number of tracks, so multi-track files such as stems read in full.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

UINT16_SIZE: Final[int] = 2
UINT32_SIZE: Final[int] = 4
# An atom header is a 32-bit size and a four-character type.
ATOM_HEADER_SIZE: Final[int] = 8
LARGE_ATOM_HEADER_SIZE: Final[int] = 16
# A size field of 1 means a 64-bit size follows the type.
LARGE_SIZE_MARKER: Final[int] = 1
FULL_ATOM_PREFIX: Final[int] = 4
MAX_CHILD_ATOMS: Final[int] = 65_536
# mvhd version 1 widens its times to 64 bits. Each layout is the offset of the timescale
# field and the struct format of the timescale and duration.
MVHD_VERSION_64_BIT: Final[int] = 1
MVHD_LAYOUT_V1: Final[tuple[int, str]] = (20, ">IQ")
MVHD_LAYOUT_V0: Final[tuple[int, str]] = (12, ">II")
# hdlr holds version and flags, then a predefined field, then the handler type.
HDLR_HANDLER_OFFSET: Final[int] = 8
# stsd holds version and flags, then an entry count, then the sample entries.
STSD_ENTRIES_OFFSET: Final[int] = FULL_ATOM_PREFIX + UINT32_SIZE
# Audio sample entries hold 28 bytes of fields before their child atoms.
SAMPLE_ENTRY_FIELDS: Final[int] = 28
ENTRY_CHANNELS_OFFSET: Final[int] = 16
ENTRY_SAMPLE_SIZE_OFFSET: Final[int] = 18
ENTRY_SAMPLE_SIZE_END: Final[int] = ENTRY_SAMPLE_SIZE_OFFSET + UINT16_SIZE
ENTRY_RATE_OFFSET: Final[int] = 24
FIXED_16_16_SHIFT: Final[int] = 16
ENCRYPTED_ENTRIES: Final[frozenset[str]] = frozenset({"drms", "drmi", "enca"})
# Offsets inside the ALAC magic cookie, after its version and flags.
ALAC_BIT_DEPTH, ALAC_CHANNELS, ALAC_BITRATE, ALAC_RATE = 5, 9, 16, 20
ALAC_COOKIE_SIZE: Final[int] = ALAC_RATE + UINT32_SIZE
ES_DESCRIPTOR_TAG, DECODER_CONFIG_TAG = 0x03, 0x04
# The ES descriptor body starts with a 16-bit ES ID and one flags byte.
ES_FLAGS_OFFSET: Final[int] = UINT16_SIZE
ES_FIXED_FIELDS: Final[int] = UINT16_SIZE + 1
# ES descriptor flags that each add an optional field after the fixed fields.
ES_DEPENDENCY_FLAG, ES_URL_FLAG, ES_OCR_FLAG = 0x80, 0x40, 0x20
DESCRIPTOR_MORE_BIT: Final[int] = 0x80
MAX_DESCRIPTOR_LENGTH_BYTES: Final[int] = 4
DECODER_AVG_BITRATE_OFFSET: Final[int] = 9
BITS_PER_BYTE: Final[int] = 8
# stsz holds version and flags, then a fixed sample size and a sample count.
STSZ_HEADER_SIZE: Final[int] = 2 * UINT32_SIZE
TEXT_UTF8, TEXT_UTF16, JPEG, PNG, BMP, SIGNED_INT, UNSIGNED_INT = 1, 2, 13, 14, 27, 21, 22
IMAGE_TYPES: Final[dict[int, str]] = {JPEG: "image/jpeg", PNG: "image/png", BMP: "image/bmp"}
PAIR_KEYS: Final[frozenset[str]] = frozenset({"trkn", "disk"})
# trkn and disk payloads hold 16 reserved bits, then a 16-bit number and a 16-bit total.
PAIR_NUMBER_OFFSET: Final[int] = UINT16_SIZE
PAIR_FIELDS_END: Final[int] = PAIR_NUMBER_OFFSET + 2 * UINT16_SIZE
DATA_HEADER_SIZE: Final[int] = 8
# The low 24 bits of a data atom's first word hold its type indicator.
DATA_TYPE_MASK: Final[int] = 0xFFFFFF
FREEFORM_KEY: Final[str] = "----"


class Mp4FormatError(ValueError):
    """Raised when bytes claim to be MP4 but do not follow the atom layout."""


@dataclass(frozen=True)
class Mp4Tag:
    """One ilst item: its key, such as \\xa9nam or ----:mean:name, and its decoded values."""

    key: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class Mp4Info:
    """Audio properties of the first sound track and the tags of an MP4 file."""

    duration: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bit_depth: int | None = None
    bitrate: int | None = None
    codec: str | None = None
    encrypted: bool = False
    tags: tuple[Mp4Tag, ...] = ()


@dataclass(frozen=True)
class _Atom:
    """One atom: its four-character type and the byte range of its body."""

    atom_type: str
    body_start: int
    end: int


@dataclass(frozen=True)
class _Track:
    """Properties read from one sound track's first sample entry."""

    codec: str
    channels: int | None
    sample_rate: int | None
    bit_depth: int | None
    bitrate: int | None
    sample_bytes: int


def read_mp4(data: bytes) -> Mp4Info:
    """Read audio properties and tags from MP4 bytes, raising Mp4FormatError on damage."""
    moov = _find(_children(data, 0, len(data)), "moov")
    if moov is None:
        raise Mp4FormatError("No moov atom found; the file is not MP4 or it is damaged.")
    moov_children = _children(data, moov.body_start, moov.end)
    duration = _movie_duration(data, _find(moov_children, "mvhd"))
    track = _first_sound_track(data, moov_children)
    tags = _read_tags(data, moov_children)
    if track is None:
        return Mp4Info(duration=duration, tags=tags)
    bitrate = track.bitrate
    if not bitrate and duration:
        bitrate = int(track.sample_bytes * BITS_PER_BYTE / duration) or None
    return Mp4Info(
        duration=duration,
        sample_rate=track.sample_rate,
        channels=track.channels,
        bit_depth=track.bit_depth,
        bitrate=bitrate,
        codec=track.codec,
        encrypted=track.codec in ENCRYPTED_ENTRIES,
        tags=tags,
    )


def _children(data: bytes, start: int, end: int) -> list[_Atom]:
    """Return the atoms laid out between start and end, raising when one overruns end."""
    atoms: list[_Atom] = []
    offset = start
    while offset + ATOM_HEADER_SIZE <= end:
        if len(atoms) >= MAX_CHILD_ATOMS:
            raise Mp4FormatError("An MP4 atom holds too many children; the file is damaged.")
        child = _read_atom(data, offset, end)
        atoms.append(child)
        offset = child.end
    return atoms


def _read_atom(data: bytes, offset: int, end: int) -> _Atom:
    """Read the atom header at offset, handling 64-bit sizes and size 0 (to end)."""
    size = struct.unpack(">I", data[offset : offset + UINT32_SIZE])[0]
    atom_type = data[offset + UINT32_SIZE : offset + ATOM_HEADER_SIZE].decode("latin-1")
    header = ATOM_HEADER_SIZE
    if size == LARGE_SIZE_MARKER:
        if offset + LARGE_ATOM_HEADER_SIZE > end:
            raise Mp4FormatError(f"The {atom_type!r} atom header is truncated.")
        size_field = data[offset + ATOM_HEADER_SIZE : offset + LARGE_ATOM_HEADER_SIZE]
        size = struct.unpack(">Q", size_field)[0]
        header = LARGE_ATOM_HEADER_SIZE
    elif size == 0:
        size = end - offset
    if size < header or offset + size > end:
        raise Mp4FormatError(f"The {atom_type!r} atom size is invalid; the file is damaged.")
    return _Atom(atom_type, offset + header, offset + size)


def _find(atoms: list[_Atom], atom_type: str) -> _Atom | None:
    """Return the first atom of atom_type in atoms, or None."""
    return next((candidate for candidate in atoms if candidate.atom_type == atom_type), None)


def _path(data: bytes, atom: _Atom | None, *types: str) -> _Atom | None:
    """Follow child types from atom, returning None when any step is missing."""
    current = atom
    for atom_type in types:
        if current is None:
            return None
        current = _find(_children(data, current.body_start, current.end), atom_type)
    return current


def _movie_duration(data: bytes, mvhd: _Atom | None) -> float | None:
    """Return the movie duration in seconds from mvhd versions 0 and 1."""
    if mvhd is None or mvhd.end - mvhd.body_start < FULL_ATOM_PREFIX:
        return None
    body = data[mvhd.body_start : mvhd.end]
    layout = MVHD_LAYOUT_V1 if body[0] == MVHD_VERSION_64_BIT else MVHD_LAYOUT_V0
    field_end = layout[0] + struct.calcsize(layout[1])
    if len(body) < field_end:
        raise Mp4FormatError("The mvhd atom is truncated; the file is damaged.")
    timescale, duration = struct.unpack(layout[1], body[layout[0] : field_end])
    return duration / timescale if timescale else None


def _first_sound_track(data: bytes, moov_children: list[_Atom]) -> _Track | None:
    """Return the properties of the first trak whose handler is soun, or None."""
    for trak in (child for child in moov_children if child.atom_type == "trak"):
        mdia = _path(data, trak, "mdia")
        handler = _path(data, mdia, "hdlr")
        if handler is None:
            continue
        handler_start = handler.body_start + HDLR_HANDLER_OFFSET
        if data[handler_start : handler_start + UINT32_SIZE] != b"soun":
            continue
        stbl = _path(data, mdia, "minf", "stbl")
        return _read_sample_table(data, stbl)
    return None


def _read_sample_table(data: bytes, stbl: _Atom | None) -> _Track | None:
    """Read the first sample entry from stsd and the sample sizes from stsz."""
    stsd = _path(data, stbl, "stsd")
    if stsd is None or stbl is None:
        return None
    entries = _children(data, stsd.body_start + STSD_ENTRIES_OFFSET, stsd.end)
    if not entries:
        return None
    entry = entries[0]
    track = _read_audio_entry(data, entry)
    stsz = _path(data, stbl, "stsz")
    sample_bytes = _total_sample_bytes(data, stsz) if stsz else 0
    return _Track(track.codec, track.channels, track.sample_rate, track.bit_depth,
                  track.bitrate, sample_bytes)  # fmt: skip


def _read_audio_entry(data: bytes, entry: _Atom) -> _Track:
    """Read channels, sample size, and rate from an audio sample entry and its children."""
    fields = data[entry.body_start : entry.body_start + SAMPLE_ENTRY_FIELDS]
    if len(fields) < SAMPLE_ENTRY_FIELDS:
        raise Mp4FormatError(f"The {entry.atom_type!r} sample entry is truncated.")
    channels = struct.unpack(">H", fields[ENTRY_CHANNELS_OFFSET:ENTRY_SAMPLE_SIZE_OFFSET])[0]
    sample_size = struct.unpack(">H", fields[ENTRY_SAMPLE_SIZE_OFFSET:ENTRY_SAMPLE_SIZE_END])[0]
    rate = struct.unpack(">I", fields[ENTRY_RATE_OFFSET:SAMPLE_ENTRY_FIELDS])[0]
    children = _children(data, entry.body_start + SAMPLE_ENTRY_FIELDS, entry.end)
    alac = _find(children, "alac")
    if alac is not None:
        return _read_alac_cookie(data, alac)
    esds = _find(children, "esds")
    bitrate = _esds_avg_bitrate(data[esds.body_start + FULL_ATOM_PREFIX : esds.end]) if esds else 0
    return _Track(entry.atom_type, channels or None, (rate >> FIXED_16_16_SHIFT) or None,
                  sample_size or None, bitrate or None, 0)  # fmt: skip


def _read_alac_cookie(data: bytes, alac: _Atom) -> _Track:
    """Read the ALAC magic cookie, which holds the full sample rate past the 16.16 limit."""
    cookie = data[alac.body_start + FULL_ATOM_PREFIX : alac.end]
    if len(cookie) < ALAC_COOKIE_SIZE:
        raise Mp4FormatError("The alac configuration atom is truncated; the file is damaged.")
    bitrate, rate = struct.unpack(">II", cookie[ALAC_BITRATE:ALAC_COOKIE_SIZE])
    return _Track("alac", cookie[ALAC_CHANNELS] or None, rate or None,
                  cookie[ALAC_BIT_DEPTH] or None, bitrate or None, 0)  # fmt: skip


def _esds_avg_bitrate(body: bytes) -> int:
    """Return the decoder config average bitrate from an esds body, or 0 when absent."""
    offset = _descriptor_body(body, 0, ES_DESCRIPTOR_TAG)
    if offset is None or offset + ES_FIXED_FIELDS > len(body):
        return 0
    flags = body[offset + ES_FLAGS_OFFSET]
    offset += ES_FIXED_FIELDS
    # Optional ES descriptor fields: a 16-bit dependency ID, a length-prefixed URL, and a
    # 16-bit OCR stream ID.
    offset += UINT16_SIZE if flags & ES_DEPENDENCY_FLAG else 0
    offset += 1 + body[offset] if flags & ES_URL_FLAG and offset < len(body) else 0
    offset += UINT16_SIZE if flags & ES_OCR_FLAG else 0
    config = _descriptor_body(body, offset, DECODER_CONFIG_TAG)
    if config is None or config + DECODER_AVG_BITRATE_OFFSET + UINT32_SIZE > len(body):
        return 0
    start = config + DECODER_AVG_BITRATE_OFFSET
    return int(struct.unpack(">I", body[start : start + UINT32_SIZE])[0])


def _descriptor_body(body: bytes, offset: int, tag: int) -> int | None:
    """Return where the body of descriptor tag at offset starts, or None when it differs.

    Descriptor lengths use up to four bytes, seven bits each, with the top bit marking more.
    """
    if offset >= len(body) or body[offset] != tag:
        return None
    offset += 1
    for _ in range(MAX_DESCRIPTOR_LENGTH_BYTES):
        if offset >= len(body):
            return None
        more = body[offset] & DESCRIPTOR_MORE_BIT
        offset += 1
        if not more:
            return offset
    return offset


def _total_sample_bytes(data: bytes, stsz: _Atom) -> int:
    """Return the total sample bytes from stsz, from its fixed size or its size table."""
    body = data[stsz.body_start + FULL_ATOM_PREFIX : stsz.end]
    if len(body) < STSZ_HEADER_SIZE:
        return 0
    fixed, count = struct.unpack(">II", body[:STSZ_HEADER_SIZE])
    if fixed:
        return fixed * count
    table = body[STSZ_HEADER_SIZE : STSZ_HEADER_SIZE + count * UINT32_SIZE]
    whole = len(table) // UINT32_SIZE
    return sum(struct.unpack(f">{whole}I", table[: whole * UINT32_SIZE]))


def _read_tags(data: bytes, moov_children: list[_Atom]) -> tuple[Mp4Tag, ...]:
    """Return the ilst items under moov.udta.meta, or an empty tuple."""
    meta = _path(data, _find(moov_children, "udta"), "meta")
    if meta is None:
        return ()
    # QuickTime-style meta atoms omit the version and flags that iTunes writes.
    type_start = meta.body_start + UINT32_SIZE
    has_prefix = data[type_start : meta.body_start + ATOM_HEADER_SIZE] != b"hdlr"
    start = meta.body_start + (FULL_ATOM_PREFIX if has_prefix else 0)
    ilst = _find(_children(data, start, meta.end), "ilst")
    if ilst is None:
        return ()
    return tuple(_read_item(data, entry) for entry in _children(data, ilst.body_start, ilst.end))


def _read_item(data: bytes, entry: _Atom) -> Mp4Tag:
    """Decode one ilst item into its key and the values of its data atoms."""
    parts = _children(data, entry.body_start, entry.end)
    key = entry.atom_type
    if key == FREEFORM_KEY:
        mean = _find(parts, "mean")
        name = _find(parts, "name")
        key = f"----:{_full_atom_text(data, mean)}:{_full_atom_text(data, name)}"
    values = tuple(
        _decode_data(entry.atom_type, data[part.body_start : part.end])
        for part in parts
        if part.atom_type == "data"
    )
    return Mp4Tag(key, values)


def _full_atom_text(data: bytes, atom: _Atom | None) -> str:
    """Return the UTF-8 text after a full atom's version and flags, or an empty string."""
    if atom is None:
        return ""
    return data[atom.body_start + FULL_ATOM_PREFIX : atom.end].decode("utf-8", errors="replace")


def _decode_data(key: str, body: bytes) -> str:
    """Decode one data atom body by its type indicator."""
    if len(body) < DATA_HEADER_SIZE:
        raise Mp4FormatError(f"A data atom in {key!r} is truncated; the file is damaged.")
    type_code = struct.unpack(">I", body[:UINT32_SIZE])[0] & DATA_TYPE_MASK
    payload = body[DATA_HEADER_SIZE:]
    if type_code == TEXT_UTF8:
        return payload.decode("utf-8", errors="replace")
    if type_code == TEXT_UTF16:
        return payload.decode("utf-16-be", errors="replace")
    if type_code in IMAGE_TYPES:
        return f"<cover {IMAGE_TYPES[type_code]} {len(payload)} bytes>"
    if type_code in (SIGNED_INT, UNSIGNED_INT) and payload:
        return str(int.from_bytes(payload, "big", signed=type_code == SIGNED_INT))
    if key in PAIR_KEYS and len(payload) >= PAIR_FIELDS_END:
        number, total = struct.unpack(">HH", payload[PAIR_NUMBER_OFFSET:PAIR_FIELDS_END])
        return f"{number}/{total}" if total else str(number)
    return f"<binary {len(payload)} bytes>"
