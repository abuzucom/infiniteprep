"""Mutagen metadata reader and writer for MP3, FLAC, WAV, and AIFF (plus read-only M4A/OGG)."""

from __future__ import annotations

import hashlib
import re
import shutil
import struct
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import mutagen
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import (
    ID3,
    TALB,
    TBPM,
    TCON,
    TDRC,
    TIT2,
    TIT3,
    TKEY,
    TPE1,
    TPE4,
    TPOS,
    TPUB,
    TRCK,
    TXXX,
)
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

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
ID3_UTF16_ENCODING: Final[int] = 1
ID3_V23_VERSION: Final[int] = 3

ILLEGAL_FILENAME_CHARS_RE: Final[re.Pattern[str]] = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE_COLLAPSE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
EMPTY_PARENS_RE: Final[re.Pattern[str]] = re.compile(r"\(\s*\)")
EMPTY_BRACKETS_RE: Final[re.Pattern[str]] = re.compile(r"\[\s*\]")

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

        audio, err_msg = self._safe_open_mutagen(file_path)
        if audio is None:
            fallback = "Unrecognized or damaged audio header; verify file integrity."
            return TechnicalAndTagMetadata(
                format=fmt,
                duration=None,
                sample_rate=None,
                channels=None,
                bitrate=None,
                warnings=[err_msg or fallback],
            )

        info = getattr(audio, "info", None)
        bits_per_sample = int(getattr(info, "bits_per_sample", 0)) or None
        sample_rate = int(getattr(info, "sample_rate", 0)) or None

        standard_tags: dict[str, str] = {}
        raw_custom_tags: dict[str, Any] = {}
        self._populate_extracted_tags(audio, standard_tags, raw_custom_tags)
        if bits_per_sample is not None:
            raw_custom_tags["_bits_per_sample"] = bits_per_sample

        return TechnicalAndTagMetadata(
            format=fmt,
            duration=float(getattr(info, "length", 0.0))
            if info and hasattr(info, "length")
            else None,
            sample_rate=sample_rate,
            channels=int(getattr(info, "channels", 0)) or None,
            bitrate=int(getattr(info, "bitrate", 0)) or None,
            standard_tags=standard_tags,
            raw_custom_tags=raw_custom_tags,
            warnings=evaluate_xdj_rx3_warnings(fmt, sample_rate, bits_per_sample),
        )

    def _safe_open_mutagen(self, file_path: Path) -> tuple[Any | None, str | None]:
        """Safely open an audio file with mutagen, returning (audio, error_message)."""
        try:
            audio = mutagen.File(str(file_path))
        except Exception as exc:
            return (
                None,
                f"Damaged or unreadable audio container ({exc.__class__.__name__}: {exc}); "
                "check source file.",
            )
        if audio is None:
            return None, "Unrecognized or damaged audio header; inspect or replace source file."
        return audio, None

    def _populate_extracted_tags(
        self,
        audio: Any,
        standard_tags: dict[str, str],
        raw_custom_tags: dict[str, Any],
    ) -> None:
        """Dispatch tag extraction based on underlying mutagen container type."""
        tags_obj = getattr(audio, "tags", None)
        if isinstance(audio, (MP3, WAVE, AIFF)) or isinstance(tags_obj, ID3):
            self._extract_id3_tags(tags_obj, standard_tags, raw_custom_tags)
            return
        if isinstance(audio, FLAC):
            self._extract_vorbis_tags(audio, standard_tags, raw_custom_tags)
            return
        if tags_obj is not None:
            for key, val in dict(tags_obj).items():
                raw_custom_tags[str(key)] = str(val)

    def _extract_id3_tags(
        self,
        id3_tags: ID3 | None,
        standard_tags: dict[str, str],
        raw_custom_tags: dict[str, Any],
    ) -> None:
        """Extract standard and custom ID3 frames into normalized dictionaries."""
        if not id3_tags:
            return
        txxx_reverse = {desc.upper(): field.value for field, desc in ID3_TXXX_MAP.items()}
        for frame_key, frame in id3_tags.items():
            self._classify_single_id3_frame(
                frame_key, frame, txxx_reverse, standard_tags, raw_custom_tags
            )

    def _classify_single_id3_frame(
        self,
        frame_key: str,
        frame: Any,
        txxx_reverse: dict[str, str],
        standard_tags: dict[str, str],
        raw_custom_tags: dict[str, Any],
    ) -> None:
        """Classify a single ID3 frame into standard_tags or raw_custom_tags."""
        base_id = frame_key.split(":", maxsplit=1)[0]
        text_list = getattr(frame, "text", None)
        first_text = str(text_list[0]).strip() if text_list else ""

        if base_id in ID3_FRAME_TO_FIELD:
            if first_text:
                standard_tags[ID3_FRAME_TO_FIELD[base_id]] = first_text
            return

        if base_id == "TXXX":
            desc = getattr(frame, "desc", "") or ""
            mapped_field = txxx_reverse.get(desc.upper())
            if mapped_field and first_text:
                standard_tags[mapped_field] = first_text
            else:
                raw_custom_tags[f"TXXX:{desc}"] = first_text
            return

        raw_custom_tags[frame_key] = [str(x) for x in text_list] if text_list else repr(frame)

    def _extract_vorbis_tags(
        self,
        flac_obj: FLAC,
        standard_tags: dict[str, str],
        raw_custom_tags: dict[str, Any],
    ) -> None:
        """Extract Vorbis comments from a FLAC file."""
        if not flac_obj.tags:
            return
        vorbis_reverse = {v.upper(): k.value for k, v in VORBIS_FIELD_MAP.items()}
        for key, values in flac_obj.tags.items():
            upper_k = key.upper()
            val_str = str(values[0]).strip() if values else ""
            if upper_k in vorbis_reverse and val_str:
                standard_tags[vorbis_reverse[upper_k]] = val_str
            else:
                raw_custom_tags[key] = list(values)

    def render_filename(
        self,
        source_path: Path,
        effective_fields: dict[str, str],
        template: str = "{artist} - {title}",
    ) -> str:
        """Render a safe filename from template and effective fields, preserving extension."""
        ext = source_path.suffix
        tokens = self._build_filename_tokens(source_path, effective_fields)
        try:
            rendered = template.format(**tokens)
        except KeyError:
            rendered = f"{tokens['artist']} - {tokens['title']}"

        rendered = EMPTY_PARENS_RE.sub("", rendered)
        rendered = EMPTY_BRACKETS_RE.sub("", rendered)
        rendered = sanitize_filename_component(rendered)
        return f"{rendered or source_path.stem}{ext}"

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
        if compute_sha256(source_path) != file_asset.content_hash:
            errors.append(
                "External modification detected: disk SHA-256 differs from imported content_hash; "
                "re-import file before writing."
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

    def apply(self, write_plan: FileWritePlan, operation_id: str) -> ChangeRecord:
        """Apply approved tags and optional rename after verified backup creation."""
        if not write_plan.is_safe_to_apply:
            reasons = "; ".join(write_plan.validation_errors + write_plan.collision_warnings)
            raise ValueError(f"Write plan failed preflight checks ({reasons}); aborting write.")

        source_path = write_plan.source_path
        current_hash = compute_sha256(source_path)
        if current_hash != write_plan.expected_content_hash:
            raise ValueError(
                "External file change detected prior to write; re-import file to refresh hash."
            )

        self._create_verified_backup(source_path, write_plan.backup_destination_path, current_hash)
        self._write_tags_by_format(source_path, write_plan.proposed_tags_to_write)
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
            backup_reference=str(write_plan.backup_destination_path),
            operation_status=OperationStatus.COMPLETED,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def _create_verified_backup(
        self, source_path: Path, backup_path: Path, expected_hash: str
    ) -> None:
        """Copy source file to backup path and verify SHA-256 integrity."""
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, backup_path)
        if compute_sha256(backup_path) != expected_hash:
            raise OSError(
                f"Backup verification failed for {backup_path}; check disk space and permissions."
            )

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
        """Write approved tags in-place for MP3, WAV, AIFF, or FLAC."""
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

    def _apply_id3_frames(self, id3: ID3, tags_to_write: dict[str, str]) -> None:
        """Apply standard ID3v2.3 UTF-16 frames while preserving PRIV:TRAKTOR4 and custom frames."""
        frame_builders: list[tuple[str, str, Any]] = [
            (FieldName.ARTIST, "TPE1", TPE1),
            (FieldName.TITLE, "TIT2", TIT2),
            (FieldName.MIX_NAME, "TIT3", TIT3),
            (FieldName.REMIXER, "TPE4", TPE4),
            (FieldName.RELEASE_TITLE, "TALB", TALB),
            (FieldName.LABEL, "TPUB", TPUB),
            (FieldName.TRACK_NUMBER, "TRCK", TRCK),
            (FieldName.DISC_NUMBER, "TPOS", TPOS),
            (FieldName.GENRE, "TCON", TCON),
            (FieldName.BPM, "TBPM", TBPM),
            (FieldName.KEY, "TKEY", TKEY),
            (FieldName.YEAR, "TDRC", TDRC),
        ]
        for field_key, frame_id, frame_cls in frame_builders:
            if field_key in tags_to_write:
                id3.delall(frame_id)
                id3.add(frame_cls(encoding=ID3_UTF16_ENCODING, text=[tags_to_write[field_key]]))

        for field_enum, txxx_desc in ID3_TXXX_MAP.items():
            if field_enum in tags_to_write:
                id3.add(
                    TXXX(
                        encoding=ID3_UTF16_ENCODING,
                        desc=txxx_desc,
                        text=[tags_to_write[field_enum]],
                    )
                )

    def _write_mp3_tags(self, file_path: Path, tags_to_write: dict[str, str]) -> None:
        """Write ID3v2.3 UTF-16 tags to an MP3 file."""
        audio = MP3(str(file_path))
        if audio.tags is None:
            audio.add_tags()
        assert isinstance(audio.tags, ID3)
        self._apply_id3_frames(audio.tags, tags_to_write)
        audio.tags.save(str(file_path), v2_version=ID3_V23_VERSION)

    def _write_wav_tags(self, file_path: Path, tags_to_write: dict[str, str]) -> None:
        """Write ID3v2.3 UTF-16 chunk and synchronize RIFF LIST-INFO chunk on a WAV file."""
        audio = WAVE(str(file_path))
        if audio.tags is None:
            audio.add_tags()
        assert isinstance(audio.tags, ID3)
        self._apply_id3_frames(audio.tags, tags_to_write)
        audio.tags.save(str(file_path), v2_version=ID3_V23_VERSION)
        synchronize_wav_list_info_chunk(file_path, tags_to_write)

    def _write_aiff_tags(self, file_path: Path, tags_to_write: dict[str, str]) -> None:
        """Write ID3v2.3 UTF-16 chunk to an AIFF file."""
        audio = AIFF(str(file_path))
        if audio.tags is None:
            audio.add_tags()
        assert isinstance(audio.tags, ID3)
        self._apply_id3_frames(audio.tags, tags_to_write)
        audio.save(v2_version=ID3_V23_VERSION)

    def _write_flac_tags(self, file_path: Path, tags_to_write: dict[str, str]) -> None:
        """Write Vorbis comments to a FLAC file, writing both LABEL and ORGANIZATION for label."""
        audio = FLAC(str(file_path))
        if audio.tags is None:
            audio.add_tags()
        for field_key, val in tags_to_write.items():
            vorbis_key = VORBIS_FIELD_MAP.get(field_key)
            if vorbis_key:
                audio[vorbis_key] = [val]
            if field_key == FieldName.LABEL:
                audio["ORGANIZATION"] = [val]
        audio.save()
