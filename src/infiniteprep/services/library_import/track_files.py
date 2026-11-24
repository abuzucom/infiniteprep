"""Read the facts of an audio file that a source library names (D333).

A source library names its audio files by path. The framework reads the facts
of a file so it can hold a library row for it. The read never changes the file.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    READ_ERRORS as PARSER_ERRORS,
)
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    MetadataTagAdapter,
    TechnicalAndTagMetadata,
)
from infiniteprep.domain.enums import AudioFormat
from infiniteprep.hashing import HASH_CHUNK_BYTES, compute_sha256
from infiniteprep.services.library_import.filesystem import (
    OpenedFile,
    identity_still_matches,
)

logger = logging.getLogger(__name__)

# A file larger than this is not read.
MAX_TRACK_FILE_BYTES: Final[int] = 2_147_483_648


@dataclass(frozen=True)
class TrackFileFacts:
    """The facts of one audio file that a library row needs."""

    file_size: int
    content_hash: str
    audio_format: AudioFormat
    duration: float | None
    original_tags: dict[str, Any]


def read_track_file_facts(
    file_path: Path, tag_adapter: MetadataTagAdapter | None = None
) -> TrackFileFacts | None:
    """Return the facts of the audio file, or None when it cannot be read.

    A file that is missing, too large, in a format the app cannot read, or
    malformed returns None. The caller holds the track as unresolved. A
    parser that fails on one file never aborts the import.
    """
    adapter = tag_adapter or MetadataTagAdapter()
    file_size = _file_size(file_path)
    if file_size is None:
        return None
    if file_size > MAX_TRACK_FILE_BYTES:
        return None
    audio_format = _detect_format(adapter, file_path)
    if audio_format is None or audio_format is AudioFormat.UNSUPPORTED:
        return None
    content_hash = _content_hash(file_path)
    if content_hash is None:
        return None
    technical_metadata = _read_metadata(adapter, file_path)
    if technical_metadata is None:
        return None
    return TrackFileFacts(
        file_size=file_size,
        content_hash=content_hash,
        audio_format=audio_format,
        duration=technical_metadata.duration,
        original_tags=_build_original_tags(technical_metadata),
    )


def read_opened_file_facts(
    opened: OpenedFile, tag_adapter: MetadataTagAdapter | None = None
) -> TrackFileFacts | None:
    """Return the facts of an open file, or None when it cannot be read.

    The facts come from the handle the file opened with, so a file that was
    replaced while it was read never reaches the app.
    """
    adapter = tag_adapter or MetadataTagAdapter()
    file_size = _opened_file_size(opened)
    if file_size is None or file_size > MAX_TRACK_FILE_BYTES:
        return None
    audio_format = _detect_format(adapter, opened.path)
    if audio_format is None or audio_format is AudioFormat.UNSUPPORTED:
        return None
    content_hash = _opened_content_hash(opened)
    if content_hash is None:
        return None
    technical_metadata = _read_metadata_from_handle(adapter, opened)
    if technical_metadata is None:
        return None
    if not identity_still_matches(opened):
        logger.warning("Import skipped one file that changed while it was read.")
        return None
    return TrackFileFacts(
        file_size=file_size,
        content_hash=content_hash,
        audio_format=audio_format,
        duration=technical_metadata.duration,
        original_tags=_build_original_tags(technical_metadata),
    )


def _opened_file_size(opened: OpenedFile) -> int | None:
    """Return the size of an open file, or None when it cannot be read."""
    try:
        return os.fstat(opened.handle.fileno()).st_size
    except OSError:
        logger.warning("Import skipped one file after an unreadable stat.")
        return None


def _opened_content_hash(opened: OpenedFile) -> str | None:
    """Return the hash of an open file, or None when it cannot be read."""
    hasher = hashlib.sha256()
    try:
        opened.handle.seek(0)
        for chunk in iter(lambda: opened.handle.read(HASH_CHUNK_BYTES), b""):
            hasher.update(chunk)
    except OSError:
        logger.warning("Import skipped one file after an unreadable hash.")
        return None
    return hasher.hexdigest()


def _file_size(file_path: Path) -> int | None:
    """Return the size of the file, or None when it cannot be read."""
    try:
        return file_path.stat().st_size
    except OSError:
        logger.warning("Import skipped one file after an unreadable stat.")
        return None


def _detect_format(adapter: MetadataTagAdapter, file_path: Path) -> AudioFormat | None:
    """Return the format of the file, or None when the parser fails.

    Only the errors the audio parser documents end the file. A defect in the
    adapter itself reaches the import boundary, so a broken import fails
    instead of dropping tracks without a word.
    """
    try:
        return adapter.detect_format(file_path)
    except PARSER_ERRORS:
        logger.warning("Import skipped one file after an unreadable format.")
        return None


def _content_hash(file_path: Path) -> str | None:
    """Return the hash of the file, or None when it cannot be read."""
    try:
        return compute_sha256(file_path)
    except OSError:
        logger.warning("Import skipped one file after an unreadable hash.")
        return None


def _read_metadata(adapter: MetadataTagAdapter, file_path: Path) -> TechnicalAndTagMetadata | None:
    """Return the metadata of the file, or None when the parser fails."""
    try:
        return adapter.read_metadata(file_path)
    except PARSER_ERRORS:
        logger.warning("Import skipped one file after an unreadable header.")
        return None


def _read_metadata_from_handle(
    adapter: MetadataTagAdapter, opened: OpenedFile
) -> TechnicalAndTagMetadata | None:
    """Return the metadata of an open file, or None when the parser fails."""
    try:
        return adapter.read_metadata_from_handle(opened.handle, opened.path)
    except PARSER_ERRORS:
        logger.warning("Import skipped one file after an unreadable header.")
        return None


def _build_original_tags(technical_metadata: Any) -> dict[str, Any]:
    """Return the structured original_tags payload from the technical metadata."""
    return {
        "standard_tags": technical_metadata.standard_tags,
        "raw_custom_tags": technical_metadata.raw_custom_tags,
        "technical": {
            "sample_rate": technical_metadata.sample_rate,
            "channels": technical_metadata.channels,
            "bitrate": technical_metadata.bitrate,
            "warnings": technical_metadata.warnings,
            "xdj_rx3_warnings": [
                warning
                for warning in technical_metadata.warnings
                if warning.startswith("XDJ-RX3 Warning")
            ],
        },
    }
