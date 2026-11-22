"""The rows of the detail panel's Technical details section."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Final

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    ID3_WRITE_FRAMES,
    VORBIS_FIELD_MAP,
)
from infiniteprep.domain.enums import AudioFormat
from infiniteprep.domain.models import ChangeRecord, FileAsset

# FLAC and Ogg store Vorbis comments. The other tagged formats store ID3 frames.
VORBIS_FORMATS: Final[frozenset[AudioFormat]] = frozenset({AudioFormat.FLAC, AudioFormat.OGG})
ID3_FORMATS: Final[frozenset[AudioFormat]] = frozenset(
    {AudioFormat.MP3, AudioFormat.WAV, AudioFormat.AIFF}
)
CONTENT_HASH_LABEL: Final[str] = "Content hash"
BACKUP_LABEL: Final[str] = "Latest backup"
DECODER_LABEL: Final[str] = "Decoder message"
NO_BACKUP_TEXT: Final[str] = "No backup yet"


def tag_frame_names(
    audio_format: AudioFormat, label_for: Callable[[str], str]
) -> list[tuple[str, str]]:
    """Return (field label, frame name) for each field the format stores in a named frame.

    label_for turns a stored field key into the plain name the panel shows.
    """
    if audio_format in VORBIS_FORMATS:
        return [(label_for(field), frame) for field, frame in VORBIS_FIELD_MAP.items()]
    if audio_format in ID3_FORMATS:
        return [(label_for(field), frame) for field, frame in ID3_WRITE_FRAMES]
    return []


def technical_detail_rows(
    asset: FileAsset,
    change_records: Sequence[ChangeRecord],
    label_for: Callable[[str], str],
) -> list[tuple[str, str]]:
    """Return the label and value of each technical detail for one track.

    change_records lists the track's journal records, newest first.
    """
    frames = tag_frame_names(asset.format, label_for)
    rows = [(f"{label} frame", frame) for label, frame in frames]
    rows.append((CONTENT_HASH_LABEL, asset.content_hash))
    latest_backup = next(
        (record.backup_reference for record in change_records if record.backup_reference), ""
    )
    rows.append((BACKUP_LABEL, latest_backup or NO_BACKUP_TEXT))
    if asset.error_message:
        rows.append((DECODER_LABEL, asset.error_message))
    return rows
