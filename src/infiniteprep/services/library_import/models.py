"""Neutral model for a DJ library that an importer reads (D333, D349).

Every format importer returns this model. The merge, the path mapping, and the
relocation search read only this model, so they stay independent of any vendor
format. A value the app has no storage for yet travels in the pending payload
until its storage issue merges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

# The field name that holds the Hot Cue Auto Load setting. The cue model issue
# owns the final name once it merges.
HOT_CUE_AUTO_LOAD_FIELD: Final[str] = "hot_cue_auto_load"

# The field names the merge writes today. Every other field stays pending.
STORED_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "title",
        "artist",
        "mix_name",
        "remixer",
        "release_title",
        "label",
        "catalog_number",
        "track_number",
        "disc_number",
        "vinyl_position",
        "genre",
        "style",
        "bpm",
        "key",
        "year",
        HOT_CUE_AUTO_LOAD_FIELD,
    }
)


class SourceCollectionKind(StrEnum):
    """The two collection kinds a source library can carry."""

    PLAYLIST = "playlist"
    CRATE = "crate"


@dataclass(frozen=True)
class ImportedValue:
    """One field value read from a source library."""

    field_name: str
    value: str


@dataclass(frozen=True)
class ImportedPendingValue:
    """One value the app has no storage for yet, such as a cue or a beat grid."""

    field_name: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImportedTrack:
    """One track read from a source library.

    source_path holds the path exactly as the source library wrote it. The path
    style of that text decides how the path mapping reads it.
    """

    source_path: str
    values: tuple[ImportedValue, ...] = ()
    pending: tuple[ImportedPendingValue, ...] = ()


@dataclass(frozen=True)
class ImportedPlaylist:
    """One playlist or crate read from a source library.

    track_paths holds source paths in the order the source library listed them.
    folder_path holds the folder names above the playlist, outermost first.
    """

    name: str
    kind: SourceCollectionKind = SourceCollectionKind.PLAYLIST
    track_paths: tuple[str, ...] = ()
    folder_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceLibrary:
    """A source library read in full by one importer."""

    source_format: str
    source_path: str
    version: str | None = None
    tracks: tuple[ImportedTrack, ...] = ()
    playlists: tuple[ImportedPlaylist, ...] = ()
    folders: tuple[tuple[str, ...], ...] = ()
