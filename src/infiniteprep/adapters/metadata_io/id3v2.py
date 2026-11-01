"""ID3v2 tag codec that reads versions 2.2, 2.3, and 2.4 and writes version 2.3.

The layouts follow the id3v2.3.0 and id3v2.4.0 structure documents. Frames this codec does
not interpret keep their body bytes unchanged, so tags from DJ software survive a rewrite.
"""

from __future__ import annotations

import datetime
import re
import struct
import zlib
from dataclasses import dataclass, field, replace
from typing import Final

ID3_MAGIC: Final[bytes] = b"ID3"
HEADER_SIZE: Final[int] = 10
FOOTER_SIZE: Final[int] = 10
FRAME_HEADER_SIZE: Final[int] = 10
V22_FRAME_HEADER_SIZE: Final[int] = 6
V22, V23, V24 = 2, 3, 4
SYNCHSAFE_LIMIT: Final[int] = 0x80
MAX_SYNCHSAFE_SIZE: Final[int] = (1 << 28) - 1
DEFAULT_PADDING_BYTES: Final[int] = 1024
MAX_PADDING_BYTES: Final[int] = 64 * 1024
# Inflation caps bound the memory a crafted compressed frame can claim. 16 MiB holds large
# cover art, and 64 MiB covers every compressed frame of one tag together.
MAX_INFLATED_FRAME_BYTES: Final[int] = 16 * 1024 * 1024
MAX_INFLATED_TAG_BYTES: Final[int] = 64 * 1024 * 1024

TAG_FLAG_UNSYNC: Final[int] = 0x80
TAG_FLAG_EXTENDED: Final[int] = 0x40
TAG_FLAG_FOOTER: Final[int] = 0x10

# Format flags of the second frame flag byte, by major version.
V23_COMPRESSED, V23_ENCRYPTED, V23_GROUPED = 0x80, 0x40, 0x20
V24_GROUPED, V24_COMPRESSED, V24_ENCRYPTED = 0x40, 0x08, 0x04
V24_UNSYNC, V24_LENGTH_INDICATOR = 0x02, 0x01
COMPRESSED_SIZE_BYTES: Final[int] = 4

LATIN1, UTF16, UTF16BE, UTF8 = 0, 1, 2, 3
CODECS: Final[dict[int, str]] = {
    LATIN1: "latin-1",
    UTF16: "utf-16",
    UTF16BE: "utf-16-be",
    UTF8: "utf-8",
}
UTF16_BOM_LE: Final[bytes] = b"\xff\xfe"
UTF16_BOM_BE: Final[bytes] = b"\xfe\xff"
V23_VALUE_SEPARATOR: Final[str] = "/"

FRAME_ID_RE: Final[re.Pattern[bytes]] = re.compile(rb"[A-Z0-9]{3,4}")
# A whole v2.4 timestamp: yyyy[-MM[-dd[THH[:mm[:ss]][Z or UTC offset]]]]. The offset only
# follows a time. Each part has a fixed width, so the optional groups cannot backtrack.
TDRC_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d{4})(?:-(\d{2})(?:-(\d{2})(?:T(\d{2})(?::(\d{2})(?::(\d{2}))?)?"
    r"(?:Z|[+-]\d{2}(?::?\d{2})?)?)?)?)?"
)
DATE_FRAMES: Final[frozenset[str]] = frozenset({"TDRC", "TDOR"})

V22_TO_V23: Final[dict[str, str]] = {
    "TT1": "TIT1", "TT2": "TIT2", "TT3": "TIT3", "TP1": "TPE1", "TP2": "TPE2",
    "TP3": "TPE3", "TP4": "TPE4", "TAL": "TALB", "TCO": "TCON", "TBP": "TBPM",
    "TKE": "TKEY", "TYE": "TYER", "TRK": "TRCK", "TPA": "TPOS", "TPB": "TPUB",
    "TCM": "TCOM", "TCR": "TCOP", "TEN": "TENC", "TLA": "TLAN", "TLE": "TLEN",
    "TSS": "TSSE", "TRC": "TSRC", "TOA": "TOPE", "TOT": "TOAL", "TOR": "TORY",
    "TDA": "TDAT", "TIM": "TIME", "TXX": "TXXX", "COM": "COMM", "ULT": "USLT",
    "UFI": "UFID", "WXX": "WXXX", "CNT": "PCNT", "POP": "POPM", "GEO": "GEOB",
    "PIC": "APIC",
}  # fmt: skip
# Frames that ID3v2.4 added and v2.3 readers do not define. Sort-order frames stay, because
# common DJ and library software writes them into v2.3 tags.
V24_ONLY_FRAMES: Final[frozenset[str]] = frozenset(
    {"ASPI", "EQU2", "RVA2", "SEEK", "SIGN", "TDEN", "TDRL", "TDTG", "TIPL", "TMCL", "TMOO",
     "TPRO", "TSST"}
)  # fmt: skip
# Frames whose key adds the description (and language) that tells repeated frames apart.
DESCRIBED_FRAMES: Final[frozenset[str]] = frozenset({"TXXX", "WXXX"})
OWNER_TERMINATOR: Final[bytes] = b"\x00"
OWNER_FRAMES: Final[frozenset[str]] = frozenset({"PRIV", "UFID", "POPM"})
LANGUAGE_FRAMES: Final[frozenset[str]] = frozenset({"COMM", "USLT"})
LANGUAGE_BYTES: Final[int] = 3
# A v2.2 PIC body holds an encoding byte, a 3-letter image format, and a picture type.
V22_PICTURE_MIN_BYTES: Final[int] = 5


class Id3FormatError(ValueError):
    """Raised when bytes claim to be an ID3v2 tag but do not follow the format."""


@dataclass(frozen=True)
class Id3Frame:
    """One frame with its body after unsynchronisation and compression are undone."""

    frame_id: str
    body: bytes
    status_flags: int = 0


@dataclass(frozen=True)
class Id3Tag:
    """An ID3v2 tag as an ordered frame list. dropped_frames names frames not kept."""

    source_version: int = V23
    frames: tuple[Id3Frame, ...] = ()
    dropped_frames: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class _InflateBudget:
    """Bytes that compressed frames of one tag may still inflate to."""

    remaining: int = MAX_INFLATED_TAG_BYTES


def decode_synchsafe(raw: bytes) -> int:
    """Decode a synchsafe integer, raising Id3FormatError when a byte has its top bit set."""
    if any(byte >= SYNCHSAFE_LIMIT for byte in raw):
        raise Id3FormatError("Synchsafe integer has a byte above 0x7F; the tag is damaged.")
    value = 0
    for byte in raw:
        value = (value << 7) | byte
    return value


def encode_synchsafe(value: int) -> bytes:
    """Encode value as a 4-byte synchsafe integer."""
    if not 0 <= value <= MAX_SYNCHSAFE_SIZE:
        raise Id3FormatError(f"Tag size {value} exceeds the ID3v2 limit; remove large frames.")
    return bytes((value >> shift) & 0x7F for shift in (21, 14, 7, 0))


def remove_unsynchronisation(data: bytes) -> bytes:
    """Reverse unsynchronisation by dropping each 0x00 that follows 0xFF."""
    return data.replace(b"\xff\x00", b"\xff")


def tag_size_from_header(header: bytes) -> int:
    """Return the full byte length of the tag that header starts, footer included."""
    if len(header) < HEADER_SIZE or header[:3] != ID3_MAGIC:
        raise Id3FormatError("Data does not start with an ID3v2 header.")
    size = HEADER_SIZE + decode_synchsafe(header[6:10])
    return size + (FOOTER_SIZE if header[3] == V24 and header[5] & TAG_FLAG_FOOTER else 0)


def parse_id3v2(data: bytes) -> Id3Tag | None:
    """Parse an ID3v2 tag from the start of data. Return None when no tag starts there."""
    if data[:3] != ID3_MAGIC:
        return None
    if len(data) < HEADER_SIZE:
        raise Id3FormatError("ID3v2 header is truncated; the file is damaged.")
    major, flags = data[3], data[5]
    if major not in (V22, V23, V24):
        raise Id3FormatError(f"ID3v2.{major} is not a supported version; use 2.2, 2.3, or 2.4.")
    body_size = decode_synchsafe(data[6:10])
    if HEADER_SIZE + body_size > len(data):
        raise Id3FormatError("ID3v2 size runs past the end of the data; the tag is truncated.")
    body = data[HEADER_SIZE : HEADER_SIZE + body_size]
    if flags & TAG_FLAG_UNSYNC and major in (V22, V23):
        body = remove_unsynchronisation(body)
    if major == V22 and flags & TAG_FLAG_EXTENDED:
        # Bit 6 in v2.2 marks a compression scheme the standard never defined.
        return Id3Tag(source_version=major, dropped_frames=("compressed ID3v2.2 tag",))
    start = _extended_header_length(body, major) if flags & TAG_FLAG_EXTENDED else 0
    # A v2.4 header flag marks every frame body as unsynchronised. Frame headers stay plain.
    forced_flags = V24_UNSYNC if major == V24 and flags & TAG_FLAG_UNSYNC else 0
    frames, dropped = _parse_frames(body, start, major, forced_flags)
    return Id3Tag(source_version=major, frames=tuple(frames), dropped_frames=tuple(dropped))


def _extended_header_length(body: bytes, major: int) -> int:
    """Return the byte length of the extended header at the start of body."""
    if len(body) < COMPRESSED_SIZE_BYTES:
        raise Id3FormatError("Extended header is truncated; the tag is damaged.")
    if major == V24:
        return decode_synchsafe(body[:4])
    return COMPRESSED_SIZE_BYTES + struct.unpack(">I", body[:4])[0]


def _parse_frames(
    body: bytes, offset: int, major: int, forced_flags: int = 0
) -> tuple[list[Id3Frame], list[str]]:
    """Walk frame headers from offset until padding, damage, or the end of body.

    forced_flags adds format flags to every frame, as the v2.4 header unsync flag requires.
    """
    frames: list[Id3Frame] = []
    dropped: list[str] = []
    header_size = V22_FRAME_HEADER_SIZE if major == V22 else FRAME_HEADER_SIZE
    id_length = 3 if major == V22 else 4
    budget = _InflateBudget()
    while offset + header_size <= len(body):
        frame_id = body[offset : offset + id_length]
        if FRAME_ID_RE.fullmatch(frame_id) is None:
            break
        size, flags = _frame_size_and_flags(body[offset : offset + header_size], major)
        start = offset + header_size
        if start + size > len(body):
            dropped.append(f"{frame_id.decode('ascii')} (truncated)")
            break
        raw = body[start : start + size]
        frame = _build_frame(frame_id.decode("ascii"), flags | forced_flags, raw, major, budget)
        if frame is None:
            dropped.append(frame_id.decode("ascii"))
        else:
            frames.append(frame)
        offset = start + size
    return frames, dropped


def _frame_size_and_flags(header: bytes, major: int) -> tuple[int, int]:
    """Return a frame's body size and its two flag bytes as one integer."""
    if major == V22:
        return int.from_bytes(header[3:6], "big"), 0
    flags = int.from_bytes(header[8:10], "big")
    if major == V24 and all(byte < SYNCHSAFE_LIMIT for byte in header[4:8]):
        return decode_synchsafe(header[4:8]), flags
    # Some v2.4 writers store plain integers, which can set the top bit of a size byte.
    return struct.unpack(">I", header[4:8])[0], flags


def _build_frame(
    frame_id: str, flags: int, raw: bytes, major: int, budget: _InflateBudget
) -> Id3Frame | None:
    """Undo frame-level encodings. Return None for frames that cannot be kept."""
    if major == V22:
        upgraded = V22_TO_V23.get(frame_id)
        if upgraded is None:
            return None
        return Id3Frame(upgraded, _upgrade_v22_picture(raw) if frame_id == "PIC" else raw)
    status, fmt = flags >> 8, flags & 0xFF
    decode_body = _decode_v24_body if major == V24 else _decode_v23_body
    body = decode_body(fmt, raw, budget)
    if body is None:
        return None
    return Id3Frame(frame_id, body, _status_as_v23(status, major))


def _decode_v23_body(fmt: int, raw: bytes, budget: _InflateBudget) -> bytes | None:
    """Undo v2.3 grouping and compression. Encrypted frames are not kept."""
    if fmt & V23_ENCRYPTED:
        return None
    body = raw
    if fmt & V23_COMPRESSED:
        body = body[COMPRESSED_SIZE_BYTES:]
    if fmt & V23_GROUPED:
        body = body[1:]
    return _inflate(body, budget) if fmt & V23_COMPRESSED else body


def _decode_v24_body(fmt: int, raw: bytes, budget: _InflateBudget) -> bytes | None:
    """Undo v2.4 grouping, length indicator, unsynchronisation, and compression."""
    if fmt & V24_ENCRYPTED:
        return None
    body = raw[1:] if fmt & V24_GROUPED else raw
    if fmt & V24_LENGTH_INDICATOR:
        body = body[COMPRESSED_SIZE_BYTES:]
    if fmt & V24_UNSYNC:
        body = remove_unsynchronisation(body)
    return _inflate(body, budget) if fmt & V24_COMPRESSED else body


def _inflate(body: bytes, budget: _InflateBudget) -> bytes | None:
    """Decompress a zlib frame body within the frame and tag caps.

    Return None when the body is damaged or would inflate past a cap.
    """
    limit = min(MAX_INFLATED_FRAME_BYTES, budget.remaining)
    inflater = zlib.decompressobj()
    try:
        # One byte past the limit shows an oversized stream without inflating the rest.
        inflated = inflater.decompress(body, limit + 1)
    except zlib.error:
        return None
    if len(inflated) > limit or not inflater.eof:
        return None
    budget.remaining -= len(inflated)
    return inflated


def _status_as_v23(status: int, major: int) -> int:
    """Move v2.4 status bits (tag alter, file alter, read only) to their v2.3 positions."""
    return (status << 1) & 0xE0 if major == V24 else status & 0xE0


def _upgrade_v22_picture(raw: bytes) -> bytes:
    """Turn a v2.2 PIC body (3-letter image format) into an APIC body (MIME type)."""
    if len(raw) < V22_PICTURE_MIN_BYTES:
        return raw
    image_format = raw[1:4].decode("latin-1").strip().lower()
    mime = {"jpg": "image/jpeg", "png": "image/png"}.get(image_format, f"image/{image_format}")
    return raw[:1] + mime.encode("latin-1") + b"\x00" + raw[4:]


def _terminator(encoding: int) -> bytes:
    """Return the string terminator for an ID3 text encoding byte."""
    return b"\x00\x00" if encoding in (UTF16, UTF16BE) else b"\x00"


def _split_strings(raw: bytes, encoding: int) -> list[bytes]:
    """Split raw on the encoding's terminator, keeping 2-byte alignment for UTF-16."""
    terminator = _terminator(encoding)
    width = len(terminator)
    parts: list[bytes] = []
    start = 0
    for index in range(0, len(raw) - width + 1, width):
        if raw[index : index + width] == terminator:
            parts.append(raw[start:index])
            start = index + width
    parts.append(raw[start:])
    return parts


def _detect_utf16_endianness(raw: bytes) -> str | None:
    """Return 'utf-16-be' or 'utf-16-le' when raw starts with a BOM, or None."""
    if raw.startswith(UTF16_BOM_BE):
        return "utf-16-be"
    if raw.startswith(UTF16_BOM_LE):
        return "utf-16-le"
    return None


def _decode_string(raw: bytes, encoding: int, default_codec: str = "utf-16-le") -> str:
    """Decode one string. A UTF-16 string without a byte order mark uses default_codec."""
    codec = CODECS.get(encoding)
    if codec is None:
        raise Id3FormatError(f"Text encoding byte {encoding} is not defined by ID3v2.")
    if encoding == UTF16:
        codec = "utf-16" if _detect_utf16_endianness(raw) else default_codec
    return raw.decode(codec, errors="replace")


def _decode_strings(
    parts: list[bytes], encoding: int, initial_codec: str = "utf-16-le"
) -> list[str]:
    """Decode string segments, preserving detected UTF-16 endianness across segments."""
    codec = initial_codec
    values: list[str] = []
    for part in parts:
        detected = _detect_utf16_endianness(part) if encoding == UTF16 else None
        if detected:
            codec = detected
        values.append(_decode_string(part, encoding, default_codec=codec))
    return values


def _split_described(body: bytes) -> tuple[int, str, bytes]:
    """Split an encoding byte, a terminated description, and the rest of a frame body."""
    if not body:
        raise Id3FormatError("Frame body is empty; expected an encoding byte.")
    encoding = body[0]
    parts = _split_strings(body[1:], encoding)
    description = _decode_string(parts[0], encoding)
    rest = body[1 + len(parts[0]) + len(_terminator(encoding)) :] if len(parts) > 1 else b""
    return encoding, description, rest


def decode_text_values(frame: Id3Frame) -> list[str]:
    """Return the strings of a text frame, without empty trailing values."""
    if not frame.body:
        return []
    encoding = frame.body[0]
    values = _decode_strings(_split_strings(frame.body[1:], encoding), encoding)
    while values and not values[-1]:
        values.pop()
    return values


def decode_txxx(frame: Id3Frame) -> tuple[str, list[str]]:
    """Return the description and values of a TXXX frame."""
    encoding, description, rest = _split_described(frame.body)
    initial_codec = _detect_utf16_endianness(frame.body[1:]) or "utf-16-le"
    values = _decode_strings(_split_strings(rest, encoding), encoding, initial_codec=initial_codec)
    while values and not values[-1]:
        values.pop()
    return description, values


def frame_key(frame: Id3Frame) -> str:
    """Return the key that tells this frame apart from others with the same identifier."""
    frame_id = frame.frame_id
    try:
        if frame_id in DESCRIBED_FRAMES:
            return f"{frame_id}:{_split_described(frame.body)[1]}"
        if frame_id in OWNER_FRAMES:
            owner = frame.body.partition(OWNER_TERMINATOR)[0].decode("latin-1")
            return f"{frame_id}:{owner}"
        if frame_id in LANGUAGE_FRAMES and len(frame.body) > LANGUAGE_BYTES:
            language, _, description, _ = _split_language_frame(frame.body)
            return f"{frame_id}:{description}:{language}"
    except Id3FormatError:
        return frame_id
    return frame_id


def encode_utf16(text: str) -> bytes:
    """Encode text as little-endian UTF-16 with a byte order mark."""
    return UTF16_BOM_LE + text.encode("utf-16-le")


def build_text_frame(frame_id: str, value: str) -> Id3Frame:
    """Build a UTF-16 text frame holding one value."""
    return Id3Frame(frame_id, bytes((UTF16,)) + encode_utf16(value))


def build_txxx_frame(description: str, value: str) -> Id3Frame:
    """Build a UTF-16 TXXX frame with one value."""
    body = bytes((UTF16,)) + encode_utf16(description) + b"\x00\x00" + encode_utf16(value)
    return Id3Frame("TXXX", body)


def replace_text_frame(tag: Id3Tag, frame_id: str, value: str) -> Id3Tag:
    """Return tag with every frame_id frame removed and one new value appended."""
    kept = tuple(frame for frame in tag.frames if frame.frame_id != frame_id)
    return replace(tag, frames=(*kept, build_text_frame(frame_id, value)))


def replace_txxx(tag: Id3Tag, description: str, value: str) -> Id3Tag:
    """Return tag with the TXXX frames of this exact description replaced by one value."""
    key = f"TXXX:{description}"
    kept = tuple(frame for frame in tag.frames if frame_key(frame) != key)
    return replace(tag, frames=(*kept, build_txxx_frame(description, value)))


def remove_frames(tag: Id3Tag, frame_id: str) -> Id3Tag:
    """Return tag without any frame of frame_id."""
    return replace(tag, frames=tuple(f for f in tag.frames if f.frame_id != frame_id))


def render_id3v23(tag: Id3Tag, padding: int = DEFAULT_PADDING_BYTES) -> bytes:
    """Render tag as ID3v2.3 with padding, converting frames that v2.3 cannot hold."""
    if not 0 <= padding <= MAX_PADDING_BYTES:
        raise ValueError(
            f"ID3v2 padding {padding} is outside 0 to {MAX_PADDING_BYTES}; pass a smaller value."
        )
    frames = _frames_for_v23(tag.frames)
    payload = b"".join(_render_frame(frame) for frame in frames) + b"\x00" * padding
    return ID3_MAGIC + bytes((V23, 0, 0)) + encode_synchsafe(len(payload)) + payload


def _render_frame(frame: Id3Frame) -> bytes:
    """Render one frame with a v2.3 header and no format flags."""
    header = frame.frame_id.encode("ascii") + struct.pack(">I", len(frame.body))
    return header + bytes((frame.status_flags & 0xE0, 0)) + frame.body


def _frames_for_v23(frames: tuple[Id3Frame, ...]) -> list[Id3Frame]:
    """Convert frames to v2.3 form. Dates move to TYER, TDAT, and TIME unless already set."""
    present = {frame.frame_id for frame in frames}
    converted: list[Id3Frame] = []
    for frame in frames:
        if frame.frame_id in DATE_FRAMES:
            converted.extend(_date_frames_for_v23(frame, present))
        elif frame.frame_id not in V24_ONLY_FRAMES:
            converted.append(_to_v23_encoding(frame))
    return converted


def _date_frames_for_v23(frame: Id3Frame, present: set[str]) -> list[Id3Frame]:
    """Convert a TDRC or TDOR frame. A timestamp without a leading year stays as it is."""
    replacements = _convert_date(frame)
    if not replacements:
        # v2.3 readers skip frames they do not define, so keeping the frame loses no data.
        return [_to_v23_encoding(frame)]
    return [replacement for replacement in replacements if replacement.frame_id not in present]


def _tdat_value(year: str, month: str | None, day: str | None) -> str | None:
    """Return the DDMM TDAT value, or None unless year, month, and day form a calendar date."""
    if month is None or day is None:
        return None
    try:
        datetime.date(int(year), int(month), int(day))
    except ValueError:
        return None
    return f"{day}{month}"


def _time_value(hour: str | None, minute: str | None, second: str | None) -> str | None:
    """Return the HHMM TIME value, or None unless the parts form a valid time of day."""
    if hour is None or minute is None:
        return None
    try:
        datetime.time(int(hour), int(minute), int(second or 0))
    except ValueError:
        return None
    return f"{hour}{minute}"


def _convert_date(frame: Id3Frame) -> list[Id3Frame]:
    """Turn a v2.4 timestamp frame into v2.3 year, date, and time frames with valid parts only.

    TIME keeps the written hour and minutes. A Z or offset suffix does not shift them.
    """
    values = decode_text_values(frame)
    match = TDRC_RE.fullmatch(values[0].strip()) if values else None
    if match is None:
        return []
    year, month, day, hour, minute, second = match.groups()
    if frame.frame_id == "TDOR":
        return [build_text_frame("TORY", year)]
    result = [build_text_frame("TYER", year)]
    tdat = _tdat_value(year, month, day)
    if tdat is not None:
        result.append(build_text_frame("TDAT", tdat))
    time_of_day = _time_value(hour, minute, second)
    if time_of_day is not None:
        result.append(build_text_frame("TIME", time_of_day))
    return result


def _to_v23_encoding(frame: Id3Frame) -> Id3Frame:
    """Re-encode text that uses an encoding v2.3 lacks, and join multiple text values."""
    frame_id = frame.frame_id
    if not frame.body:
        return frame
    if frame_id == "TXXX":
        description, values = decode_txxx(frame)
        return replace(build_txxx_frame(description, V23_VALUE_SEPARATOR.join(values)),
                       status_flags=frame.status_flags)  # fmt: skip
    if frame_id.startswith("T"):
        values = decode_text_values(frame)
        if frame.body[0] in (LATIN1, UTF16) and len(values) <= 1:
            return frame
        value = V23_VALUE_SEPARATOR.join(values)
        return replace(build_text_frame(frame_id, value), status_flags=frame.status_flags)
    if frame_id in LANGUAGE_FRAMES and frame.body[0] in (UTF16BE, UTF8):
        return replace(frame, body=_reencode_language_frame(frame.body))
    return frame


def _split_language_frame(body: bytes) -> tuple[str, int, str, bytes]:
    """Split a COMM or USLT body into its language, encoding, description, and text bytes."""
    if len(body) < 1 + LANGUAGE_BYTES:
        raise Id3FormatError(
            "Language frame body is truncated; expected encoding byte and 3-byte language."
        )
    language = body[1 : 1 + LANGUAGE_BYTES].decode("latin-1")
    encoding, description, rest = _split_described(body[:1] + body[1 + LANGUAGE_BYTES :])
    return language, encoding, description, rest


def _reencode_language_frame(body: bytes) -> bytes:
    """Re-encode a COMM or USLT body from UTF-8 or UTF-16BE to UTF-16 with a BOM."""
    language, encoding, description, rest = _split_language_frame(body)
    text = _decode_string(rest.rstrip(b"\x00") if encoding in (LATIN1, UTF8) else rest, encoding)
    return (
        bytes((UTF16,)) + language.encode("latin-1") + encode_utf16(description) + b"\x00\x00"
        + encode_utf16(text.rstrip("\x00"))
    )  # fmt: skip
