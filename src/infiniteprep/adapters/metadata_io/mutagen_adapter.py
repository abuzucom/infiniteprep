"""Metadata reader and writer for MP3, FLAC, WAV, and AIFF, plus read-only M4A and OGG.

Reads and writes use the in-house codecs in this package. The class and module keep the
MutagenAdapter name, because callers import them by that name.
"""

from __future__ import annotations

import hashlib
import logging
import mmap
import os
import re
import shutil
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from infiniteprep import __version__
from infiniteprep.adapters.metadata_io.audio_properties import (
    AudioProperties,
    read_aiff_stream,
    read_flac_stream,
    read_mp3_properties,
    read_wav_stream,
)
from infiniteprep.adapters.metadata_io.flac_vorbis import (
    VorbisComment,
    parse_vorbis_comment,
    read_flac_comment,
    replace_field,
    write_flac_comment,
)
from infiniteprep.adapters.metadata_io.id3_containers import (
    read_aiff_tag,
    read_mp3_tag,
    read_wav_tag,
    write_aiff_tag,
    write_mp3_tag,
    write_wav_tag,
)
from infiniteprep.adapters.metadata_io.id3v2 import (
    LANGUAGE_FRAMES,
    Id3FormatError,
    Id3Frame,
    Id3Tag,
    decode_language_text,
    decode_text_values,
    decode_txxx,
    frame_key,
    parse_id3v2,
    remove_frames,
    replace_text_frame,
    replace_txxx,
)
from infiniteprep.adapters.metadata_io.mp4_reader import read_mp4
from infiniteprep.adapters.metadata_io.ogg_reader import read_ogg
from infiniteprep.domain.enums import (
    WRITE_SUPPORTED_FORMATS,
    AudioFormat,
    FieldName,
    OperationStatus,
)
from infiniteprep.domain.models import ApprovedField, ChangeRecord, FileAsset
from infiniteprep.domain.ports import FileWritePlan, TechnicalAndTagMetadata

HASH_CHUNK_BYTES: Final[int] = 65536
XDJ_RX3_MAX_SAMPLE_RATE_HZ: Final[int] = 48000
XDJ_RX3_MAX_BIT_DEPTH: Final[int] = 24
FLAC_VENDOR: Final[str] = f"InfinitePrep {__version__}"

ILLEGAL_FILENAME_CHARS_RE: Final[re.Pattern[str]] = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE_COLLAPSE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
EMPTY_PARENS_RE: Final[re.Pattern[str]] = re.compile(r"\(\s*\)")
EMPTY_BRACKETS_RE: Final[re.Pattern[str]] = re.compile(r"\[\s*\]")
# Rename templates accept bare {token} names and the {{ and }} literal brace
# escapes of str.format. str.format would also evaluate attribute, index, and
# conversion syntax from user settings. Any other brace selects the default.
TEMPLATE_PART_RE: Final[re.Pattern[str]] = re.compile(r"\{\{|\}\}|\{([a-z_]*)\}|[{}]")
LITERAL_BRACE_ESCAPES: Final[dict[str, str]] = {"{{": "{", "}}": "}"}

logger = logging.getLogger(__name__)

EXTENSION_TO_FORMAT: Final[dict[str, AudioFormat]] = {
    ".mp3": AudioFormat.MP3,
    ".flac": AudioFormat.FLAC,
    ".wav": AudioFormat.WAV,
    ".wave": AudioFormat.WAV,
    ".aif": AudioFormat.AIFF,
    ".aiff": AudioFormat.AIFF,
    ".m4a": AudioFormat.M4A,
    ".mp4": AudioFormat.M4A,
    ".ogg": AudioFormat.OGG,
}

VORBIS_FIELD_MAP: Final[dict[str, str]] = {
    FieldName.ARTIST: "ARTIST",
    FieldName.TITLE: "TITLE",
    FieldName.MIX_NAME: "SUBTITLE",
    FieldName.REMIXER: "REMIXER",
    FieldName.RELEASE_TITLE: "ALBUM",
    FieldName.LABEL: "LABEL",
    FieldName.CATALOG_NUMBER: "CATALOGNUMBER",
    FieldName.TRACK_NUMBER: "TRACKNUMBER",
    FieldName.DISC_NUMBER: "DISCNUMBER",
    FieldName.VINYL_POSITION: "VINYLTRACK",
    FieldName.GENRE: "GENRE",
    FieldName.STYLE: "STYLE",
    FieldName.BPM: "BPM",
    FieldName.KEY: "INITIALKEY",
    FieldName.YEAR: "DATE",
    FieldName.ACOUSTID_ID: "ACOUSTID_ID",
    FieldName.MB_RECORDING_ID: "MUSICBRAINZ_TRACKID",
    FieldName.MB_RELEASE_ID: "MUSICBRAINZ_ALBUMID",
    FieldName.DISCOGS_RELEASE_ID: "DISCOGS_RELEASE_ID",
}

ID3_TXXX_MAP: Final[dict[str, str]] = {
    FieldName.CATALOG_NUMBER: "CATALOGNUMBER",
    FieldName.VINYL_POSITION: "VINYLTRACK",
    FieldName.STYLE: "STYLE",
    FieldName.ACOUSTID_ID: "Acoustid Id",
    FieldName.MB_RECORDING_ID: "MusicBrainz Release Track Id",
    FieldName.MB_RELEASE_ID: "MusicBrainz Album Id",
    FieldName.DISCOGS_RELEASE_ID: "DISCOGS_RELEASE_ID",
}

ID3_FRAME_TO_FIELD: Final[dict[str, str]] = {
    "TPE1": FieldName.ARTIST.value,
    "TIT2": FieldName.TITLE.value,
    "TIT3": FieldName.MIX_NAME.value,
    "TPE4": FieldName.REMIXER.value,
    "TALB": FieldName.RELEASE_TITLE.value,
    "TPUB": FieldName.LABEL.value,
    "TRCK": FieldName.TRACK_NUMBER.value,
    "TPOS": FieldName.DISC_NUMBER.value,
    "TCON": FieldName.GENRE.value,
    "TBPM": FieldName.BPM.value,
    "TKEY": FieldName.KEY.value,
    "TDRC": FieldName.YEAR.value,
    "TYER": FieldName.YEAR.value,
}

RIFF_INFO_TAG_MAP: Final[dict[str, bytes]] = {
    FieldName.TITLE.value: b"INAM",
    FieldName.ARTIST.value: b"IART",
    FieldName.RELEASE_TITLE.value: b"IPRD",
    FieldName.GENRE.value: b"IGNR",
    FieldName.YEAR.value: b"ICRD",
}


ID3_STORED_FIELDS: Final[frozenset[str]] = frozenset({*ID3_FRAME_TO_FIELD.values(), *ID3_TXXX_MAP})
VORBIS_STORED_FIELDS: Final[frozenset[str]] = frozenset(VORBIS_FIELD_MAP)
BACKUP_NAME_FORBIDDEN_CHARS: Final[frozenset[str]] = frozenset({"/", "\\", ":"})
FIRST_PRINTABLE_ASCII: Final[int] = 0x20


class SourceChangedDuringWriteError(OSError):
    """Raised when another program changes the source before the written copy replaces it.

    No tags reach the source, so a rollback must not restore the backup over the new edit.
    """


def _require_plain_name(component: str) -> str:
    """Return component, or raise ValueError when it could name a different directory.

    Backslash and colon are refused on every platform, because a library can move between
    POSIX and Windows. Windows reads them as a separator and a drive or stream marker.
    """
    if (
        not component.strip()
        or component in {".", ".."}
        or Path(component).name != component
        or any(
            char in BACKUP_NAME_FORBIDDEN_CHARS or ord(char) < FIRST_PRINTABLE_ASCII
            for char in component
        )
    ):
        raise ValueError(
            f"Backup name part {component!r} is not a plain file name; refusing to build the "
            "backup path."
        )
    return component


def operation_backup_path(
    backup_dir: Path, file_id: str, operation_id: str, file_name: str, kind: str = ""
) -> Path:
    """Return the backup path for one operation, so a later backup never replaces this one."""
    parts = [file_id, operation_id, *([kind] if kind else []), file_name]
    return backup_dir / "_".join(_require_plain_name(part) for part in parts)


def copy_to_new_verified_file(source: Path, destination: Path, expected_hash: str) -> None:
    """Copy source to a destination that must not exist yet, then verify its SHA-256.

    Exclusive creation closes the gap between an existence check and the copy. A copy that
    fails or does not verify is removed, so no unverified backup stays behind.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    verified = False
    try:
        with destination.open("xb") as destination_handle:
            created = True
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, destination_handle)
        shutil.copystat(source, destination)
        verified = compute_sha256(destination) == expected_hash
    except FileExistsError as exc:
        # Only the exclusive open raises this, so the existing file is not ours to remove.
        raise FileExistsError(
            f"Backup {destination} already exists; refusing to replace it. Retry the write."
        ) from exc
    finally:
        # Any exit before a matching hash, an interrupt included, removes the partial copy.
        if created and not verified:
            destination.unlink(missing_ok=True)
    if not verified:
        raise OSError(
            f"Backup verification failed for {destination}; check disk space and permissions."
        )


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of an entire file."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def sanitize_filename_component(value: str) -> str:
    """Remove characters illegal on Windows, macOS, or Linux filesystems."""
    cleaned = ILLEGAL_FILENAME_CHARS_RE.sub("_", value)
    cleaned = WHITESPACE_COLLAPSE_RE.sub(" ", cleaned).strip().rstrip(".")
    return cleaned


def evaluate_xdj_rx3_warnings(
    fmt: AudioFormat, sample_rate: int | None, bits_per_sample: int | None
) -> list[str]:
    """Return non-blocking Pioneer XDJ-RX3 hardware compatibility warnings."""
    warnings: list[str] = []
    if fmt == AudioFormat.OGG:
        warnings.append(
            "XDJ-RX3 Warning: OGG Vorbis is not supported by Pioneer XDJ-RX3 hardware; "
            "convert to WAV or FLAC before USB export."
        )
    if sample_rate is not None and sample_rate > XDJ_RX3_MAX_SAMPLE_RATE_HZ:
        warnings.append(
            f"XDJ-RX3 Warning: Sample rate {sample_rate} Hz exceeds Pioneer XDJ-RX3 maximum "
            f"({XDJ_RX3_MAX_SAMPLE_RATE_HZ} Hz); convert to 48 kHz or 44.1 kHz."
        )
    if bits_per_sample is not None and bits_per_sample > XDJ_RX3_MAX_BIT_DEPTH:
        warnings.append(
            f"XDJ-RX3 Warning: {bits_per_sample}-bit depth exceeds Pioneer XDJ-RX3 maximum "
            f"({XDJ_RX3_MAX_BIT_DEPTH}-bit); convert 32-bit float to 24-bit PCM."
        )
    return warnings


def build_riff_list_info_chunk(tags_to_write: dict[str, str]) -> bytes:
    """Build a valid RIFF LIST-INFO chunk for WAV files so standalone hardware reads basic tags."""
    subchunks: list[bytes] = []
    for field_name, fourcc in RIFF_INFO_TAG_MAP.items():
        raw_val = tags_to_write.get(field_name, "").strip()
        if not raw_val:
            continue
        encoded = raw_val.encode("utf-8", errors="replace") + b"\x00"
        padded = encoded + (b"\x00" if len(encoded) % 2 == 1 else b"")
        subchunks.append(fourcc + struct.pack("<I", len(encoded)) + padded)
    if not subchunks:
        return b""
    body = b"INFO" + b"".join(subchunks)
    padded_body = body + (b"\x00" if len(body) % 2 == 1 else b"")
    return b"LIST" + struct.pack("<I", len(body)) + padded_body


def _strip_existing_riff_list_info_chunks(raw_bytes: bytes) -> list[bytes]:
    """Return all RIFF WAVE subchunks except existing LIST-INFO chunks."""
    kept_chunks: list[bytes] = []
    offset = 12
    total_len = len(raw_bytes)
    while offset + 8 <= total_len:
        chunk_id = raw_bytes[offset : offset + 4]
        chunk_size = struct.unpack("<I", raw_bytes[offset + 4 : offset + 8])[0]
        if offset + 8 + chunk_size > total_len:
            raise ValueError("Malformed WAV file: RIFF subchunk size exceeds file bounds.")
        chunk_end = min(total_len, offset + 8 + chunk_size + (chunk_size % 2))
        if not (chunk_id == b"LIST" and raw_bytes[offset + 8 : offset + 12] == b"INFO"):
            kept_chunks.append(raw_bytes[offset:chunk_end])
        offset = chunk_end
    return kept_chunks


MAX_WAV_REWRITE_BYTES: Final[int] = 1_073_741_824  # 1 GiB


def synchronize_wav_list_info_chunk(file_path: Path, tags_to_write: dict[str, str]) -> None:
    """Synchronize the RIFF LIST-INFO chunk in a WAV file without modifying the PCM data chunk."""
    new_list_chunk = build_riff_list_info_chunk(tags_to_write)
    if not new_list_chunk:
        return
    if file_path.stat().st_size > MAX_WAV_REWRITE_BYTES:
        raise ValueError(
            f"WAV file '{file_path.name}' exceeds maximum LIST-INFO rewrite size limit."
        )
    raw_bytes = file_path.read_bytes()
    if len(raw_bytes) < 12 or raw_bytes[:4] != b"RIFF" or raw_bytes[8:12] != b"WAVE":
        return
    riff_size = struct.unpack("<I", raw_bytes[4:8])[0]
    if riff_size + 8 > len(raw_bytes):
        raise ValueError("Malformed WAV file: RIFF header size exceeds file bounds.")

    kept_chunks = _strip_existing_riff_list_info_chunks(raw_bytes)
    kept_chunks.append(new_list_chunk)
    wave_payload = b"WAVE" + b"".join(kept_chunks)
    file_path.write_bytes(b"RIFF" + struct.pack("<I", len(wave_payload)) + wave_payload)


# Each codec raises a ValueError subclass on damage. Slicing a short buffer can raise
# struct.error or IndexError, and opening or mapping the file can raise OSError.
READ_ERRORS: Final[tuple[type[Exception], ...]] = (ValueError, struct.error, IndexError, OSError)
PICTURE_FRAME: Final[str] = "APIC"
FLAC_ORGANIZATION_FIELD: Final[str] = "ORGANIZATION"
# Field order and frame IDs for the mapped ID3 text fields. YEAR writes as v2.4 TDRC, which the
# v2.3 renderer splits into TYER, TDAT, and TIME.
ID3_WRITE_FRAMES: Final[tuple[tuple[str, str], ...]] = (
    (FieldName.ARTIST, "TPE1"),
    (FieldName.TITLE, "TIT2"),
    (FieldName.MIX_NAME, "TIT3"),
    (FieldName.REMIXER, "TPE4"),
    (FieldName.RELEASE_TITLE, "TALB"),
    (FieldName.LABEL, "TPUB"),
    (FieldName.TRACK_NUMBER, "TRCK"),
    (FieldName.DISC_NUMBER, "TPOS"),
    (FieldName.GENRE, "TCON"),
    (FieldName.BPM, "TBPM"),
    (FieldName.KEY, "TKEY"),
    (FieldName.YEAR, "TDRC"),
)
V23_DATE_FRAMES: Final[tuple[str, ...]] = ("TYER", "TDAT", "TIME")
DATE_FRAME_IDS: Final[frozenset[str]] = frozenset({*V23_DATE_FRAMES, "TDRC"})
# TYER holds YYYY, TDAT holds DDMM, and TIME holds HHMM. Each two-digit field ends at
# PAIR_SPLIT, so the length checks and the slices share one width.
PAIR_SPLIT: Final[int] = 2
YEAR_DIGITS: Final[int] = 4
TDAT_DIGITS: Final[int] = PAIR_SPLIT * 2
TIME_DIGITS: Final[int] = PAIR_SPLIT * 2
MIME_TERMINATOR: Final[bytes] = b"\x00"


@dataclass(frozen=True)
class _ParsedAudio:
    """Stream properties and tags read from one file."""

    properties: AudioProperties
    standard_tags: dict[str, str]
    raw_custom_tags: dict[str, Any]


def _parse_audio_file(file_path: Path, fmt: AudioFormat) -> _ParsedAudio:
    """Read properties and tags with the in-house codecs, mapping the file read-only.

    The map keeps large WAV and AIFF files on disk. Only headers and tag bytes are read.
    """
    if fmt == AudioFormat.OGG:
        return _parse_ogg(file_path)
    parser = MAPPED_PARSERS[fmt]
    with (
        file_path.open("rb") as handle,
        mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped,
    ):
        # mmap slices, indexes, and searches like bytes, which is all the codecs do.
        return parser(mapped)


def _parse_mp3(data: bytes) -> _ParsedAudio:
    """Read MP3 timing from its frames and tags from the leading ID3v2 tag."""
    standard, raw = _classify_id3_tag(read_mp3_tag(data))
    return _ParsedAudio(read_mp3_properties(data), standard, raw)


def _parse_wav(data: bytes) -> _ParsedAudio:
    """Read WAV properties and the ID3v2 tag in its id3 chunk."""
    stream = read_wav_stream(data)
    tag = parse_id3v2(stream.tag_body) if stream.tag_body else None
    standard, raw = _classify_id3_tag(tag)
    return _ParsedAudio(stream.properties, standard, raw)


def _parse_aiff(data: bytes) -> _ParsedAudio:
    """Read AIFF properties and the ID3v2 tag in its ID3 chunk."""
    stream = read_aiff_stream(data)
    tag = parse_id3v2(stream.tag_body) if stream.tag_body else None
    standard, raw = _classify_id3_tag(tag)
    return _ParsedAudio(stream.properties, standard, raw)


def _parse_flac(data: bytes) -> _ParsedAudio:
    """Read FLAC properties from STREAMINFO and tags from its Vorbis comment."""
    stream = read_flac_stream(data)
    comment = parse_vorbis_comment(stream.tag_body) if stream.tag_body else None
    standard, raw = _classify_vorbis_comment(comment)
    return _ParsedAudio(stream.properties, standard, raw)


def _parse_m4a(data: bytes) -> _ParsedAudio:
    """Read M4A properties and keep every ilst item as a raw custom tag."""
    info = read_mp4(data)
    properties = AudioProperties(
        info.duration, info.sample_rate, info.channels, info.bitrate, info.bit_depth
    )
    raw: dict[str, Any] = {tag.key: str(list(tag.values)) for tag in info.tags}
    return _ParsedAudio(properties, {}, raw)


def _parse_ogg(file_path: Path) -> _ParsedAudio:
    """Read Ogg properties and keep every comment field as a raw custom tag.

    read_ogg accepts bytes or a path only, so the file is read whole. Ogg files are lossy and
    small next to WAV or AIFF.
    """
    info = read_ogg(file_path)
    properties = AudioProperties(
        info.duration, info.sample_rate, info.channels, info.bitrate or None, None
    )
    grouped = _group_vorbis_fields(info.comment)
    return _ParsedAudio(properties, {}, {key: str(values) for key, values in grouped.items()})


MAPPED_PARSERS: Final[dict[AudioFormat, Callable[[bytes], _ParsedAudio]]] = {
    AudioFormat.MP3: _parse_mp3,
    AudioFormat.WAV: _parse_wav,
    AudioFormat.AIFF: _parse_aiff,
    AudioFormat.FLAC: _parse_flac,
    AudioFormat.M4A: _parse_m4a,
}


def _apply_id3_fields(tag: Id3Tag | None, tags_to_write: dict[str, str]) -> Id3Tag:
    """Replace the mapped text and TXXX frames. Every other frame, PRIV:TRAKTOR4 included, stays.

    A YEAR write also drops TYER, TDAT, and TIME. The v2.3 renderer keeps those frames when
    present, so an old date would otherwise win over the new TDRC.
    """
    updated = tag or Id3Tag()
    if FieldName.YEAR in tags_to_write:
        for frame_id in V23_DATE_FRAMES:
            updated = remove_frames(updated, frame_id)
    for field_key, frame_id in ID3_WRITE_FRAMES:
        if field_key in tags_to_write:
            updated = replace_text_frame(updated, frame_id, tags_to_write[field_key])
    for field_key, description in ID3_TXXX_MAP.items():
        if field_key in tags_to_write:
            updated = replace_txxx(updated, description, tags_to_write[field_key])
    return updated


def _classify_id3_tag(tag: Id3Tag | None) -> tuple[dict[str, str], dict[str, Any]]:
    """Sort ID3 frames into mapped standard fields and raw custom tags."""
    standard: dict[str, str] = {}
    raw: dict[str, Any] = {}
    if tag is None:
        return standard, raw
    txxx_reverse = {desc.upper(): field.value for field, desc in ID3_TXXX_MAP.items()}
    for frame in tag.frames:
        try:
            _classify_id3_frame(frame, txxx_reverse, standard, raw)
        except Id3FormatError:
            # A frame whose text cannot decode still appears, as a byte summary.
            key, summary = _binary_frame_entry(frame)
            raw[key] = summary
    _join_v23_date(tag, standard, raw)
    return standard, raw


def _join_v23_date(tag: Id3Tag, standard: dict[str, str], raw: dict[str, Any]) -> None:
    """Rebuild YEAR as YYYY-MM-DD and THH:MM from TYER, TDAT, and TIME, as mutagen did.

    The v2.3 writer splits a full date across these frames. Rejoining them lets a written
    date read back whole. A TDRC frame, as in v2.4 tags, already holds the full date.
    """
    date_frames = [frame for frame in tag.frames if frame.frame_id in DATE_FRAME_IDS]
    try:
        texts = {frame.frame_id: decode_text_values(frame) for frame in date_frames}
    except Id3FormatError:
        # A date frame that cannot decode leaves YEAR as the per-frame pass set it.
        return
    year = (texts.get("TYER") or [""])[0].strip()
    if "TDRC" in texts or len(year) != YEAR_DIGITS or not year.isdigit():
        return
    day_month = (texts.get("TDAT") or [""])[0].strip()
    if len(day_month) != TDAT_DIGITS or not day_month.isdigit():
        return
    joined = f"{year}-{day_month[PAIR_SPLIT:]}-{day_month[:PAIR_SPLIT]}"
    raw.pop("TDAT", None)
    clock = (texts.get("TIME") or [""])[0].strip()
    if len(clock) == TIME_DIGITS and clock.isdigit():
        joined += f"T{clock[:PAIR_SPLIT]}:{clock[PAIR_SPLIT:]}"
        raw.pop("TIME", None)
    standard[FieldName.YEAR.value] = joined


def _classify_id3_frame(
    frame: Id3Frame,
    txxx_reverse: dict[str, str],
    standard: dict[str, str],
    raw: dict[str, Any],
) -> None:
    """Place one ID3 frame in standard or raw. Empty mapped text frames are skipped."""
    frame_id = frame.frame_id
    if frame_id == "TXXX":
        description, values = decode_txxx(frame)
        first = values[0].strip() if values else ""
        mapped = txxx_reverse.get(description.upper())
        if mapped and first:
            standard[mapped] = first
        else:
            raw[f"TXXX:{description}"] = first
        return
    if frame_id.startswith("T"):
        values = decode_text_values(frame)
        if frame_id not in ID3_FRAME_TO_FIELD:
            raw[frame_id] = values
        elif values and values[0].strip():
            standard[ID3_FRAME_TO_FIELD[frame_id]] = values[0].strip()
        return
    if frame_id in LANGUAGE_FRAMES:
        raw[frame_key(frame)] = decode_language_text(frame)
        return
    key, summary = _binary_frame_entry(frame)
    raw[key] = summary


def _binary_frame_entry(frame: Id3Frame) -> tuple[str, str]:
    """Return a key and a summary with the length and SHA-256 of a frame body.

    The digest lets a later read tell whether the frame changed without storing its bytes.
    """
    key = frame_key(frame)
    label = key.replace(":", " ", 1)
    if frame.frame_id == PICTURE_FRAME:
        mime, description = _picture_fields(frame.body)
        key, label = f"{PICTURE_FRAME}:{description}", f"{PICTURE_FRAME} {mime}"
    digest = hashlib.sha256(frame.body).hexdigest()
    return key, f"<{label}, {len(frame.body)} bytes, sha256 {digest}>"


def _picture_fields(body: bytes) -> tuple[str, str]:
    """Return the MIME type and description of an APIC body, or empty strings when damaged.

    APIC holds an encoding byte, a Latin-1 MIME type with a terminator, and a picture type
    byte, then a description in the frame encoding. The description and the rest split the
    same way a TXXX body does, so decode_txxx reads it.
    """
    mime_end = body.find(MIME_TERMINATOR, 1)
    if mime_end < 0:
        return "", ""
    mime = body[1:mime_end].decode("latin-1")
    described = body[:1] + body[mime_end + 2 :]
    try:
        return mime, decode_txxx(Id3Frame(PICTURE_FRAME, described))[0]
    except Id3FormatError:
        return mime, ""


def _group_vorbis_fields(comment: VorbisComment | None) -> dict[str, list[str]]:
    """Group comment values by lowercased field name, keeping their order."""
    grouped: dict[str, list[str]] = {}
    if comment is None:
        return grouped
    for name, value in comment.fields:
        grouped.setdefault(name.lower(), []).append(value)
    return grouped


def _classify_vorbis_comment(
    comment: VorbisComment | None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Sort Vorbis comment fields into mapped standard fields and raw custom tags."""
    standard: dict[str, str] = {}
    raw: dict[str, Any] = {}
    vorbis_reverse = {name.upper(): field.value for field, name in VORBIS_FIELD_MAP.items()}
    for key, values in _group_vorbis_fields(comment).items():
        mapped = vorbis_reverse.get(key.upper())
        first = values[0].strip()
        if mapped and first:
            standard[mapped] = first
        else:
            raw[key] = values
    return standard, raw


class MutagenAdapter:
    """Reads and writes audio metadata without re-encoding audio streams."""

    supported_write_formats: frozenset[AudioFormat] = WRITE_SUPPORTED_FORMATS

    def detect_format(self, file_path: Path) -> AudioFormat:
        """Detect AudioFormat from file extension."""
        return EXTENSION_TO_FORMAT.get(file_path.suffix.lower(), AudioFormat.UNSUPPORTED)

    def read_metadata(self, file_path: Path) -> TechnicalAndTagMetadata:
        """Read technical audio properties and existing tags without modifying the file."""
        fmt = self.detect_format(file_path)
        if fmt == AudioFormat.UNSUPPORTED:
            msg = f"Unsupported file extension: {file_path.suffix}; use a supported format."
            return TechnicalAndTagMetadata(
                format=AudioFormat.UNSUPPORTED,
                duration=None,
                sample_rate=None,
                channels=None,
                bitrate=None,
                warnings=[msg],
            )

        try:
            parsed = _parse_audio_file(file_path, fmt)
        except READ_ERRORS as exc:
            return TechnicalAndTagMetadata(
                format=fmt,
                duration=None,
                sample_rate=None,
                channels=None,
                bitrate=None,
                warnings=[
                    f"Damaged or unreadable audio container ({exc.__class__.__name__}: {exc}); "
                    "check source file."
                ],
            )

        props = parsed.properties
        raw_custom_tags = parsed.raw_custom_tags
        if props.bits_per_sample is not None:
            raw_custom_tags["_bits_per_sample"] = props.bits_per_sample
        return TechnicalAndTagMetadata(
            format=fmt,
            duration=props.duration,
            sample_rate=props.sample_rate,
            channels=props.channels,
            bitrate=props.bitrate,
            standard_tags=parsed.standard_tags,
            raw_custom_tags=raw_custom_tags,
            warnings=evaluate_xdj_rx3_warnings(fmt, props.sample_rate, props.bits_per_sample),
        )

    def render_filename(
        self,
        source_path: Path,
        effective_fields: dict[str, str],
        template: str = "{artist} - {title}",
    ) -> str:
        """Render a safe filename from template and effective fields, preserving extension."""
        ext = source_path.suffix
        tokens = self._build_filename_tokens(source_path, effective_fields)
        rendered = self._substitute_template_tokens(template, tokens)

        rendered = EMPTY_PARENS_RE.sub("", rendered)
        rendered = EMPTY_BRACKETS_RE.sub("", rendered)
        rendered = sanitize_filename_component(rendered)
        return f"{rendered or source_path.stem}{ext}"

    @staticmethod
    def _substitute_template_tokens(template: str, tokens: dict[str, str]) -> str:
        """Replace known {token} names and brace escapes. Other brace syntax uses the default."""
        pieces: list[str] = []
        position = 0
        for match in TEMPLATE_PART_RE.finditer(template):
            replacement = LITERAL_BRACE_ESCAPES.get(match.group(0))
            if replacement is None:
                replacement = tokens.get(match.group(1) or "")
            if replacement is None:
                # repr() escapes line breaks in the template text.
                logger.warning(
                    "Rename template %r has unsupported brace syntax %r. "
                    "Using the default 'artist - title' layout.",
                    template,
                    match.group(0),
                )
                return f"{tokens['artist']} - {tokens['title']}"
            pieces.append(template[position : match.start()])
            pieces.append(replacement)
            position = match.end()
        pieces.append(template[position:])
        return "".join(pieces)

    def _build_filename_tokens(
        self, source_path: Path, effective_fields: dict[str, str]
    ) -> dict[str, str]:
        """Build sanitized template replacement tokens from effective tag fields."""
        artist_raw = effective_fields.get(FieldName.ARTIST, "") or "Unknown Artist"
        title_raw = effective_fields.get(FieldName.TITLE, "") or source_path.stem
        return {
            "artist": sanitize_filename_component(artist_raw),
            "title": sanitize_filename_component(title_raw),
            "mix_name": sanitize_filename_component(effective_fields.get(FieldName.MIX_NAME, "")),
            "remixer": sanitize_filename_component(effective_fields.get(FieldName.REMIXER, "")),
            "label": sanitize_filename_component(effective_fields.get(FieldName.LABEL, "")),
            "catalog_number": sanitize_filename_component(
                effective_fields.get(FieldName.CATALOG_NUMBER, "")
            ),
            "bpm": sanitize_filename_component(effective_fields.get(FieldName.BPM, "")),
            "key": sanitize_filename_component(effective_fields.get(FieldName.KEY, "")),
            "track_number": sanitize_filename_component(
                effective_fields.get(FieldName.TRACK_NUMBER, "")
            ),
            "vinyl_position": sanitize_filename_component(
                effective_fields.get(FieldName.VINYL_POSITION, "")
            ),
        }

    def preview(
        self,
        file_asset: FileAsset,
        approved_fields: dict[str, ApprovedField],
        backup_dir: Path,
        rename_template: str | None = None,
        enable_rename: bool = False,
    ) -> FileWritePlan:
        """Build a preflighted FileWritePlan without modifying any file on disk."""
        source_path = Path(file_asset.current_path)
        validation_errors = self._validate_source_for_write(file_asset, source_path)
        existing_std = dict(file_asset.original_tags.get("standard_tags", {}))
        preserved_custom = dict(file_asset.original_tags.get("raw_custom_tags", {}))

        proposed_to_write = {
            fname: str(af.approved_value).strip()
            for fname, af in approved_fields.items()
            if af.approved_value is not None and str(af.approved_value).strip() != ""
        }
        effective_tags = {**existing_std, **proposed_to_write}
        destination_path, collision_warnings = self._resolve_destination_path(
            source_path, effective_tags, rename_template, enable_rename
        )
        backup_destination_path = backup_dir / f"{file_asset.id}_{source_path.name}"

        return FileWritePlan(
            file_id=file_asset.id,
            source_path=source_path,
            destination_path=destination_path,
            rename_enabled=enable_rename and (destination_path != source_path),
            expected_content_hash=file_asset.content_hash,
            existing_tags=existing_std,
            proposed_tags_to_write=proposed_to_write,
            preserved_custom_tags=preserved_custom,
            backup_destination_path=backup_destination_path,
            collision_warnings=collision_warnings,
            validation_errors=validation_errors,
        )

    def _validate_source_for_write(self, file_asset: FileAsset, source_path: Path) -> list[str]:
        """Validate write format support, file existence, and content hash match."""
        errors: list[str] = []
        if not file_asset.is_write_supported:
            errors.append(
                f"Format '{file_asset.format}' is not enabled for tag writing; "
                "use MP3, FLAC, WAV, or AIFF."
            )
        if not source_path.exists():
            errors.append(f"Source file missing at {source_path}; re-import or locate file.")
            return errors
        errors.extend(self._validate_replaceable(source_path))
        if compute_sha256(source_path) != file_asset.content_hash:
            errors.append(
                "External modification detected: disk SHA-256 differs from imported content_hash; "
                "re-import file before writing."
            )
        return errors

    @staticmethod
    def _validate_replaceable(source_path: Path) -> list[str]:
        """Refuse files that a swap of a written copy would detach from their other names."""
        if source_path.is_symlink():
            return [
                "File is a symbolic link; writing would replace the link with a copy. "
                "Import the link target instead."
            ]
        errors: list[str] = []
        stat_result = source_path.stat()
        if stat_result.st_nlink > 1:
            errors.append(
                f"File has {stat_result.st_nlink} hard links; writing would split them. "
                "Copy the file to a single-link location before writing."
            )
        # Windows has no getuid. NTFS ownership does not change when a copy replaces a file.
        get_uid = getattr(os, "getuid", None)
        if get_uid is not None and stat_result.st_uid != get_uid():
            errors.append(
                "File is owned by another user; writing would change its owner. "
                "Ask the owner to write it, or take ownership first."
            )
        return errors

    def _resolve_destination_path(
        self,
        source_path: Path,
        effective_tags: dict[str, str],
        rename_template: str | None,
        enable_rename: bool,
    ) -> tuple[Path, list[str]]:
        """Compute destination path and check for filename collisions within parent folder."""
        if not enable_rename or not rename_template:
            return source_path, []
        new_filename = self.render_filename(source_path, effective_tags, rename_template)
        destination_path = source_path.with_name(new_filename).resolve()
        allowed_root = source_path.parent.resolve()
        if not destination_path.is_relative_to(allowed_root):
            return source_path, ["Destination path escapes source directory boundary; blocked."]
        if destination_path != source_path.resolve() and destination_path.exists():
            return destination_path, [
                f"Destination file already exists ({destination_path}); collision blocked."
            ]
        return destination_path, []

    def apply(
        self, write_plan: FileWritePlan, operation_id: str, backup_path: Path | None = None
    ) -> ChangeRecord:
        """Apply approved tags and optional rename after verified backup creation.

        backup_path names the backup the caller journaled. Without it, the per-operation name
        in the plan's backup folder applies.
        """
        if not write_plan.is_safe_to_apply:
            reasons = "; ".join(write_plan.validation_errors + write_plan.collision_warnings)
            raise ValueError(f"Write plan failed preflight checks ({reasons}); aborting write.")

        source_path = write_plan.source_path
        current_hash = compute_sha256(source_path)
        if current_hash != write_plan.expected_content_hash:
            raise ValueError(
                "External file change detected prior to write; re-import file to refresh hash."
            )
        # The file can gain a link or change owner between preview and apply.
        replace_errors = self._validate_replaceable(source_path)
        if replace_errors:
            raise ValueError(f"{'; '.join(replace_errors)} Aborting write.")

        if backup_path is None:
            backup_path = operation_backup_path(
                write_plan.backup_destination_path.parent,
                write_plan.file_id,
                operation_id,
                source_path.name,
            )
        self._create_verified_backup(source_path, backup_path, current_hash)
        self._write_verified_copy(
            source_path, write_plan.proposed_tags_to_write, operation_id, current_hash
        )
        final_path = self._execute_optional_rename(source_path, write_plan)
        post_hash = compute_sha256(final_path)

        return ChangeRecord(
            operation_id=operation_id,
            file_id=write_plan.file_id,
            previous_path=str(source_path),
            new_path=str(final_path),
            pre_write_hash=current_hash,
            post_write_hash=post_hash,
            previous_tags=write_plan.existing_tags,
            written_tags=write_plan.proposed_tags_to_write,
            backup_reference=str(backup_path),
            operation_status=OperationStatus.COMPLETED,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def _write_verified_copy(
        self,
        source_path: Path,
        tags_to_write: dict[str, str],
        operation_id: str,
        expected_hash: str,
    ) -> None:
        """Write tags to a sibling copy, verify it, then atomically swap it over the source.

        A crash at any point leaves the source with its original bytes. The copy name depends
        only on the operation id and the suffix, which detect_format reads.
        """
        staged = source_path.with_name(f".{operation_id}.staged{source_path.suffix}")
        try:
            shutil.copy2(source_path, staged)
            baseline = self.read_metadata(staged)
            self._write_tags_by_format(staged, tags_to_write)
            self._verify_written_copy(baseline, staged, tags_to_write)
            self._ensure_source_unchanged(source_path, expected_hash)
            os.replace(staged, source_path)
        finally:
            self._discard_staged_copy(staged)

    @staticmethod
    def _ensure_source_unchanged(source_path: Path, expected_hash: str) -> None:
        """Raise when another program changed the source while its copy was written."""
        if compute_sha256(source_path) != expected_hash:
            raise SourceChangedDuringWriteError(
                "The file changed in another program during the write; the change is kept and "
                "no tags were written. Re-import the file and retry."
            )

    @staticmethod
    def _discard_staged_copy(staged: Path) -> None:
        """Remove a leftover staged copy without hiding the error that ended the write."""
        try:
            staged.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove staged copy %s (%s); delete it by hand.", staged, exc)

    def _verify_written_copy(
        self, baseline: TechnicalAndTagMetadata, staged: Path, tags_to_write: dict[str, str]
    ) -> None:
        """Check the copy parses, keeps its audio properties, and reads back each written tag."""
        after = self.read_metadata(staged)
        problems: list[str] = []
        if after.duration is None:
            problems.append("the audio no longer parses")
        if (after.sample_rate, after.channels) != (baseline.sample_rate, baseline.channels):
            problems.append("the sample rate or channel count changed")
        stored = VORBIS_STORED_FIELDS if after.format == AudioFormat.FLAC else ID3_STORED_FIELDS
        problems.extend(
            f"{field_name} reads back as {after.standard_tags.get(field_name)!r}"
            for field_name, value in tags_to_write.items()
            if field_name in stored and after.standard_tags.get(field_name) != value
        )
        if problems:
            raise OSError(
                f"Written copy failed verification ({'; '.join(problems)}); the original file "
                "is unchanged. Check the file with another tag editor and retry."
            )

    def _create_verified_backup(
        self, source_path: Path, backup_path: Path, expected_hash: str
    ) -> None:
        """Copy source file to a new backup path and verify SHA-256 integrity."""
        copy_to_new_verified_file(source_path, backup_path, expected_hash)

    def _execute_optional_rename(self, source_path: Path, write_plan: FileWritePlan) -> Path:
        """Perform atomic rename if enabled, refusing to overwrite any existing file."""
        if not write_plan.rename_enabled or write_plan.destination_path == source_path:
            return source_path
        if write_plan.destination_path.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing file {write_plan.destination_path}; "
                "adjust rename template."
            )
        source_path.rename(write_plan.destination_path)
        return write_plan.destination_path

    def _write_tags_by_format(self, file_path: Path, tags_to_write: dict[str, str]) -> None:
        """Write approved tags in-place for MP3, WAV, AIFF, or FLAC.

        Each writer reads the whole file into memory, so files past the rewrite limit are
        refused before any read.
        """
        size = file_path.stat().st_size
        if size > MAX_WAV_REWRITE_BYTES:
            raise ValueError(
                f"'{file_path.name}' is {size} bytes, past the {MAX_WAV_REWRITE_BYTES}-byte tag "
                "write limit; no tags were written. Split or re-encode the file, then retry."
            )
        fmt = self.detect_format(file_path)
        if fmt == AudioFormat.MP3:
            self._write_mp3_tags(file_path, tags_to_write)
            return
        if fmt == AudioFormat.WAV:
            self._write_wav_tags(file_path, tags_to_write)
            return
        if fmt == AudioFormat.AIFF:
            self._write_aiff_tags(file_path, tags_to_write)
            return
        if fmt == AudioFormat.FLAC:
            self._write_flac_tags(file_path, tags_to_write)
            return
        raise ValueError(f"Unsupported write format {fmt}; use MP3, FLAC, WAV, or AIFF.")

    def _write_mp3_tags(self, file_path: Path, tags_to_write: dict[str, str]) -> None:
        """Write ID3v2.3 UTF-16 tags to an MP3 file. Every byte after the tag stays the same."""
        data = file_path.read_bytes()
        tag = _apply_id3_fields(read_mp3_tag(data), tags_to_write)
        file_path.write_bytes(write_mp3_tag(data, tag))

    def _write_wav_tags(self, file_path: Path, tags_to_write: dict[str, str]) -> None:
        """Write the ID3v2.3 UTF-16 chunk of a WAV file, then synchronize its LIST-INFO chunk."""
        data = file_path.read_bytes()
        tag = _apply_id3_fields(read_wav_tag(data), tags_to_write)
        file_path.write_bytes(write_wav_tag(data, tag))
        synchronize_wav_list_info_chunk(file_path, tags_to_write)

    def _write_aiff_tags(self, file_path: Path, tags_to_write: dict[str, str]) -> None:
        """Write the ID3v2.3 UTF-16 chunk of an AIFF file."""
        data = file_path.read_bytes()
        tag = _apply_id3_fields(read_aiff_tag(data), tags_to_write)
        file_path.write_bytes(write_aiff_tag(data, tag))

    def _write_flac_tags(self, file_path: Path, tags_to_write: dict[str, str]) -> None:
        """Write Vorbis comments to a FLAC file, writing both LABEL and ORGANIZATION for label."""
        data = file_path.read_bytes()
        comment = read_flac_comment(data) or VorbisComment(vendor=FLAC_VENDOR)
        for field_key, value in tags_to_write.items():
            vorbis_key = VORBIS_FIELD_MAP.get(field_key)
            if vorbis_key:
                comment = replace_field(comment, vorbis_key, [value])
            if field_key == FieldName.LABEL:
                comment = replace_field(comment, FLAC_ORGANIZATION_FIELD, [value])
        file_path.write_bytes(write_flac_comment(data, comment))
