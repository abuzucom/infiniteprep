"""Hash encoded audio bytes independently of writable tags."""

from __future__ import annotations

import hashlib
import mmap
from collections.abc import Callable
from pathlib import Path
from typing import Final

from infiniteprep.adapters.metadata_io.audio_properties import (
    find_aiff_payload,
    find_flac_payload,
    find_m4a_payload,
    find_mp3_payload,
    find_wav_payload,
)
from infiniteprep.domain.enums import AudioFormat

RANGE_LENGTH_BYTES: Final[int] = 8
PAYLOAD_LOCATORS: Final[dict[AudioFormat, Callable[[bytes], tuple[tuple[int, int], ...]]]] = {
    AudioFormat.MP3: find_mp3_payload,
    AudioFormat.FLAC: find_flac_payload,
    AudioFormat.WAV: find_wav_payload,
    AudioFormat.AIFF: find_aiff_payload,
    AudioFormat.M4A: find_m4a_payload,
}


class AudioPayloadVerificationError(OSError):
    """A tag write could not verify the audio in its staging file."""


def compute_audio_payload_digest(path: Path, audio_format: AudioFormat) -> bytes:
    """Hash each audio range in order without copying the mapped payload."""
    try:
        return _hash_mapped_payload(path, audio_format)
    except (OSError, ValueError) as exc:
        raise AudioPayloadVerificationError(
            f"Could not identify the audio payload for verification ({exc}). "
            "Check the file in a player before retrying the tag write."
        ) from exc


def _hash_mapped_payload(path: Path, audio_format: AudioFormat) -> bytes:
    """Release all mapped views before the caller rewrites the file."""
    digest = hashlib.sha256()
    with (
        path.open("rb") as handle,
        mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped,
        memoryview(mapped) as view,
    ):
        for start, end in PAYLOAD_LOCATORS[audio_format](mapped):
            # Preserve range boundaries if the format holds multiple audio chunks.
            digest.update((end - start).to_bytes(RANGE_LENGTH_BYTES, "big"))
            with view[start:end] as payload:
                digest.update(payload)
    return digest.digest()
