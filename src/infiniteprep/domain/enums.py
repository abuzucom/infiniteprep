"""Core domain enumerations and canonical key representations."""

from __future__ import annotations

from enum import StrEnum


class AudioFormat(StrEnum):
    MP3 = "mp3"
    FLAC = "flac"
    WAV = "wav"
    AIFF = "aiff"
    M4A = "m4a"
    OGG = "ogg"
    UNSUPPORTED = "unsupported"


WRITE_SUPPORTED_FORMATS: frozenset[AudioFormat] = frozenset(
    {
        AudioFormat.MP3,
        AudioFormat.FLAC,
        AudioFormat.WAV,
        AudioFormat.AIFF,
    }
)

READ_SUPPORTED_FORMATS: frozenset[AudioFormat] = frozenset(
    {
        AudioFormat.MP3,
        AudioFormat.FLAC,
        AudioFormat.WAV,
        AudioFormat.AIFF,
        AudioFormat.M4A,
        AudioFormat.OGG,
    }
)


class GroupingBasis(StrEnum):
    FOLDER_HYPOTHESIS = "folder_hypothesis"
    USER_ASSIGNED = "user_assigned"
    SINGLE_FILE = "single_file"


class SourceType(StrEnum):
    VINYL = "vinyl"
    CASSETTE = "cassette"
    CD_RIP = "cd_rip"
    DIGITAL = "digital"
    UNKNOWN = "unknown"


class EntityType(StrEnum):
    RECORDING = "recording"
    RELEASE = "release"


class TaskStatus(StrEnum):
    """Execution status of background tasks/jobs (kept separate from ReviewStatus)."""

    IDLE = "idle"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    """Workflow review state of a FileAsset (Section 7.E)."""

    UNPROCESSED = "unprocessed"
    PROCESSING = "processing"
    PROPOSED = "proposed"
    READY_FOR_REVIEW = "ready_for_review"
    CONFLICTING_CANDIDATES = "conflicting_candidates"
    PARTIALLY_IDENTIFIED = "partially_identified"
    PARTIALLY_APPROVED = "partially_approved"
    NO_MATCH = "no_match"
    APPROVED = "approved"
    READY_TO_WRITE = "ready_to_write"
    APPLIED = "applied"
    WRITTEN = "written"
    ERROR = "error"


class ProposalReviewStatus(StrEnum):
    """Review status of an individual FieldProposal."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ApprovalOrigin(StrEnum):
    PROPOSAL_ACCEPTED = "proposal_accepted"
    USER_EDITED = "user_edited"
    MANUAL_EDIT = "manual_edit"
    ORIGINAL_KEPT = "original_kept"


class OperationStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    BACKED_UP = "backed_up"
    TAGS_WRITTEN = "tags_written"
    RENAMED = "renamed"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    RESTORED = "restored"


class FieldName(StrEnum):
    ARTIST = "artist"
    TITLE = "title"
    MIX_NAME = "mix_name"
    REMIXER = "remixer"
    RELEASE_TITLE = "release_title"
    LABEL = "label"
    CATALOG_NUMBER = "catalog_number"
    TRACK_NUMBER = "track_number"
    DISC_NUMBER = "disc_number"
    VINYL_POSITION = "vinyl_position"
    GENRE = "genre"
    STYLE = "style"
    BPM = "bpm"
    KEY = "key"
    YEAR = "year"
    ACOUSTID_ID = "acoustid_id"
    MB_RECORDING_ID = "mb_recording_id"
    MB_RELEASE_ID = "mb_release_id"
    DISCOGS_RELEASE_ID = "discogs_release_id"


class PitchClass(StrEnum):
    C = "C"
    C_SHARP = "C#"
    D = "D"
    E_FLAT = "Eb"
    E = "E"
    F = "F"
    F_SHARP = "F#"
    G = "G"
    A_FLAT = "Ab"
    A = "A"
    B_FLAT = "Bb"
    B = "B"


class KeyMode(StrEnum):
    MAJOR = "major"
    MINOR = "minor"


class KeyDisplayNotation(StrEnum):
    STANDARD = "standard"  # e.g., F#m, A, Bbm
    CAMELOT = "camelot"  # e.g., 11A, 11B, 3A
    OPEN_KEY = "open_key"  # e.g., 4m, 4d, 8m


# Canonical mapping from (PitchClass, KeyMode) to (Standard, Camelot, OpenKey)
_KEY_NOTATION_MAP: dict[tuple[PitchClass, KeyMode], tuple[str, str, str]] = {
    (PitchClass.A_FLAT, KeyMode.MINOR): ("Abm", "1A", "6m"),
    (PitchClass.B, KeyMode.MAJOR): ("B", "1B", "6d"),
    (PitchClass.E_FLAT, KeyMode.MINOR): ("Ebm", "2A", "7m"),
    (PitchClass.F_SHARP, KeyMode.MAJOR): ("F#", "2B", "7d"),
    (PitchClass.B_FLAT, KeyMode.MINOR): ("Bbm", "3A", "8m"),
    (PitchClass.C_SHARP, KeyMode.MAJOR): ("Db", "3B", "8d"),
    (PitchClass.F, KeyMode.MINOR): ("Fm", "4A", "9m"),
    (PitchClass.A_FLAT, KeyMode.MAJOR): ("Ab", "4B", "9d"),
    (PitchClass.C, KeyMode.MINOR): ("Cm", "5A", "10m"),
    (PitchClass.E_FLAT, KeyMode.MAJOR): ("Eb", "5B", "10d"),
    (PitchClass.G, KeyMode.MINOR): ("Gm", "6A", "11m"),
    (PitchClass.B_FLAT, KeyMode.MAJOR): ("Bb", "6B", "11d"),
    (PitchClass.D, KeyMode.MINOR): ("Dm", "7A", "12m"),
    (PitchClass.F, KeyMode.MAJOR): ("F", "7B", "12d"),
    (PitchClass.A, KeyMode.MINOR): ("Am", "8A", "1m"),
    (PitchClass.C, KeyMode.MAJOR): ("C", "8B", "1d"),
    (PitchClass.E, KeyMode.MINOR): ("Em", "9A", "2m"),
    (PitchClass.G, KeyMode.MAJOR): ("G", "9B", "2d"),
    (PitchClass.B, KeyMode.MINOR): ("Bm", "10A", "3m"),
    (PitchClass.D, KeyMode.MAJOR): ("D", "10B", "3d"),
    (PitchClass.F_SHARP, KeyMode.MINOR): ("F#m", "11A", "4m"),
    (PitchClass.A, KeyMode.MAJOR): ("A", "11B", "4d"),
    (PitchClass.C_SHARP, KeyMode.MINOR): ("C#m", "12A", "5m"),
    (PitchClass.E, KeyMode.MAJOR): ("E", "12B", "5d"),
}

_ENHARMONIC_LOOKUP: dict[str, PitchClass] = {
    "c": PitchClass.C,
    "c#": PitchClass.C_SHARP,
    "db": PitchClass.C_SHARP,
    "d": PitchClass.D,
    "d#": PitchClass.E_FLAT,
    "eb": PitchClass.E_FLAT,
    "e": PitchClass.E,
    "f": PitchClass.F,
    "f#": PitchClass.F_SHARP,
    "gb": PitchClass.F_SHARP,
    "g": PitchClass.G,
    "g#": PitchClass.A_FLAT,
    "ab": PitchClass.A_FLAT,
    "a": PitchClass.A,
    "a#": PitchClass.B_FLAT,
    "bb": PitchClass.B_FLAT,
    "b": PitchClass.B,
}


def format_canonical_key(
    tonic: PitchClass | str | None,
    mode: KeyMode | str | None,
    notation: KeyDisplayNotation = KeyDisplayNotation.STANDARD,
) -> str:
    """Format a canonical (tonic, mode) pair into the requested display notation."""
    if not tonic or not mode:
        return "unknown"
    try:
        pc = PitchClass(tonic) if isinstance(tonic, str) else tonic
        km = KeyMode(mode) if isinstance(mode, str) else mode
    except ValueError:
        return f"{tonic} {mode}".strip()

    std_val, camelot_val, open_val = _KEY_NOTATION_MAP[(pc, km)]
    if notation == KeyDisplayNotation.CAMELOT:
        return camelot_val
    if notation == KeyDisplayNotation.OPEN_KEY:
        return open_val
    return std_val


def _parse_two_part_key(tonic_raw: str, mode_raw: str) -> tuple[PitchClass, KeyMode] | None:
    """Parse two-token key strings such as 'F# minor' or 'Eb major'."""
    pitch_class = _ENHARMONIC_LOOKUP.get(tonic_raw)
    if not pitch_class:
        return None
    if mode_raw.startswith("min") or mode_raw == "m":
        return (pitch_class, KeyMode.MINOR)
    if mode_raw.startswith("maj"):
        return (pitch_class, KeyMode.MAJOR)
    return None


def _parse_single_token_key(token: str) -> tuple[PitchClass, KeyMode] | None:
    """Parse single-token key strings such as 'F#m', 'Dmin', 'Amaj', or 'C'."""
    if token.endswith("min"):
        pitch_class = _ENHARMONIC_LOOKUP.get(token[:-3])
        return (pitch_class, KeyMode.MINOR) if pitch_class else None
    if token.endswith("maj"):
        pitch_class = _ENHARMONIC_LOOKUP.get(token[:-3])
        return (pitch_class, KeyMode.MAJOR) if pitch_class else None
    if token.endswith("m") and len(token) >= 2:
        pitch_class = _ENHARMONIC_LOOKUP.get(token[:-1])
        return (pitch_class, KeyMode.MINOR) if pitch_class else None
    pitch_class = _ENHARMONIC_LOOKUP.get(token)
    return (pitch_class, KeyMode.MAJOR) if pitch_class else None


def parse_key_string(raw_key: str | None) -> tuple[PitchClass, KeyMode] | None:
    """Parse standard, Camelot, or Open Key strings into canonical (PitchClass, KeyMode)."""
    if not raw_key:
        return None
    cleaned = raw_key.strip()
    if not cleaned:
        return None

    upper = cleaned.upper()
    for (pitch_class, key_mode), (std_v, cam_v, open_v) in _KEY_NOTATION_MAP.items():
        if upper in (std_v.upper(), cam_v.upper(), open_v.upper()):
            return (pitch_class, key_mode)

    lower = cleaned.lower().replace("-", " ").replace("_", " ")
    parts = lower.split()
    if len(parts) == 2:
        return _parse_two_part_key(parts[0], parts[1])
    if len(parts) == 1:
        return _parse_single_token_key(parts[0])
    return None
