"""USB export JSON manifest persistence and canonical My Tags digest (D116-D121, D139-D148)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

USB_METADATA_DIR_NAME: Final[str] = ".infiniteprep"
COMPLETED_MANIFEST_FILE_NAME: Final[str] = "manifest.json"
IN_PROGRESS_MANIFEST_FILE_NAME: Final[str] = "manifest.in-progress.json"
MANIFEST_STATE_IN_PROGRESS: Final[str] = "in_progress"
MANIFEST_STATE_COMPLETED: Final[str] = "completed"


class ExportScope(StrEnum):
    """Supported library selection scopes for a USB export (D143, D144)."""

    FULL_LIBRARY = "full_library"
    SINGLE_PLAYLIST = "single_playlist"
    MULTIPLE_PLAYLISTS = "multiple_playlists"


@dataclass(frozen=True)
class ManifestTrackRef:
    """Stable exported track reference recorded in the USB manifest (D148)."""

    relative_path: str
    title: str
    artist: str


@dataclass(frozen=True)
class ManifestTrackOmission:
    """Single omitted field value recorded per exported track (D146, D147, D148)."""

    track: ManifestTrackRef
    field_name: str
    source_value: str
    reason: str


@dataclass(frozen=True)
class ManifestReaderResult:
    """Metadata reader identity and outcome stored in the manifest (D179)."""

    name: str
    version: str
    result: str


@dataclass(frozen=True)
class ManifestTargetCounts:
    """Per-target outcome counts for a completed or interrupted export (D145)."""

    exported: int = 0
    exported_with_omissions: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ManifestTagEntry:
    """One My Tags vocabulary node included in the canonical digest (D119)."""

    category: str
    name: str
    parent: str = ""


@dataclass(frozen=True)
class ManifestTrackTags:
    """Assigned My Tags for one relative track path (D119)."""

    relative_path: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class UsbExportManifest:
    """Latest completed or temporary in-progress USB export manifest."""

    export_id: str
    state: str
    started_at_utc: str
    completed_at_utc: str | None
    app_version: str
    adapter_versions: dict[str, str]
    analysis_versions: dict[str, str]
    readers: tuple[ManifestReaderResult, ...]
    scope: ExportScope
    playlist_names: tuple[str, ...]
    track_count: int
    target_counts: dict[str, ManifestTargetCounts]
    omissions: tuple[ManifestTrackOmission, ...]
    my_tags_sha256: str


def compute_my_tags_digest(
    vocabulary: Sequence[ManifestTagEntry],
    assignments: Sequence[ManifestTrackTags],
) -> str:
    """Compute SHA-256 over canonicalized My Tags vocabulary and assignments (D119, D120)."""
    vocab_rows = sorted(
        (
            {"category": item.category, "name": item.name, "parent": item.parent}
            for item in vocabulary
        ),
        key=lambda row: (row["category"], row["name"], row["parent"]),
    )
    assign_rows = sorted(
        (
            {"relative_path": item.relative_path, "tags": sorted(item.tags)}
            for item in assignments
        ),
        key=lambda row: str(row["relative_path"]),
    )
    payload = {"assignments": assign_rows, "vocabulary": vocab_rows}
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def metadata_dir_path(mount_root: Path) -> Path:
    """Return the confined InfinitePrep metadata directory below mount_root (D142)."""
    base = mount_root.resolve()
    if not base.exists() or not base.is_dir():
        raise ValueError(f"Mount root is missing or not a directory: {mount_root}")
    candidate = mount_root / USB_METADATA_DIR_NAME
    if candidate.is_symlink():
        raise ValueError("USB metadata folder cannot be a symbolic link.")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base):
        raise ValueError("USB metadata folder resolves outside the mount root.")
    return candidate


def _confined_manifest_file(mount_root: Path, file_name: str) -> Path:
    """Return a confined manifest file path inside the mount root's metadata folder."""
    meta_dir = metadata_dir_path(mount_root)
    target = meta_dir / file_name
    if target.is_symlink():
        raise ValueError(f"USB manifest path cannot be a symbolic link: {file_name}")
    resolved = target.resolve()
    if not resolved.is_relative_to(mount_root.resolve()):
        raise ValueError("USB manifest path resolves outside the mount root.")
    return target


def _write_json_atomic(target: Path, payload: Mapping[str, object]) -> None:
    """Atomically write JSON UTF-8 content to target inside an existing parent."""
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", text=True
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise


def _manifest_to_dict(manifest: UsbExportManifest) -> dict[str, object]:
    """Convert a UsbExportManifest into a JSON-serializable mapping."""
    data = asdict(manifest)
    data["scope"] = manifest.scope.value
    return data


def _require_str(raw: object, field_name: str) -> str:
    """Validate that raw is a string value."""
    if not isinstance(raw, str):
        raise ValueError(f"Manifest field '{field_name}' must be a string.")
    return raw


def _parse_str_dict(raw: object, field_name: str) -> dict[str, str]:
    """Validate and copy a string-to-string dictionary."""
    if not isinstance(raw, Mapping):
        raise ValueError(f"Manifest field '{field_name}' must be an object.")
    parsed: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"Manifest field '{field_name}' must contain string pairs.")
        parsed[key] = value
    return parsed


def _parse_readers(raw: object) -> tuple[ManifestReaderResult, ...]:
    """Parse reader result entries from manifest JSON."""
    if not isinstance(raw, list):
        raise ValueError("Manifest field 'readers' must be a list.")
    items: list[ManifestReaderResult] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ValueError("Each manifest reader entry must be an object.")
        items.append(
            ManifestReaderResult(
                name=_require_str(entry.get("name"), "readers.name"),
                version=_require_str(entry.get("version"), "readers.version"),
                result=_require_str(entry.get("result"), "readers.result"),
            )
        )
    return tuple(items)


def _parse_target_counts(raw: object) -> dict[str, ManifestTargetCounts]:
    """Parse per-target result count records from manifest JSON."""
    if not isinstance(raw, Mapping):
        raise ValueError("Manifest field 'target_counts' must be an object.")
    parsed: dict[str, ManifestTargetCounts] = {}
    for target_name, counts in raw.items():
        if not isinstance(target_name, str) or not isinstance(counts, Mapping):
            raise ValueError("Invalid target_counts entry in manifest.")
        parsed[target_name] = ManifestTargetCounts(
            exported=int(counts.get("exported", 0)),
            exported_with_omissions=int(counts.get("exported_with_omissions", 0)),
            skipped=int(counts.get("skipped", 0)),
            failed=int(counts.get("failed", 0)),
        )
    return parsed


def _parse_omissions(raw: object) -> tuple[ManifestTrackOmission, ...]:
    """Parse per-track omission entries from manifest JSON."""
    if not isinstance(raw, list):
        raise ValueError("Manifest field 'omissions' must be a list.")
    omissions: list[ManifestTrackOmission] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ValueError("Each omission entry must be an object.")
        track_raw = entry.get("track")
        if not isinstance(track_raw, Mapping):
            raise ValueError("Omission 'track' must be an object.")
        track_ref = ManifestTrackRef(
            relative_path=_require_str(track_raw.get("relative_path"), "track.relative_path"),
            title=_require_str(track_raw.get("title"), "track.title"),
            artist=_require_str(track_raw.get("artist"), "track.artist"),
        )
        omissions.append(
            ManifestTrackOmission(
                track=track_ref,
                field_name=_require_str(entry.get("field_name"), "omissions.field_name"),
                source_value=_require_str(entry.get("source_value"), "omissions.source_value"),
                reason=_require_str(entry.get("reason"), "omissions.reason"),
            )
        )
    return tuple(omissions)


def _manifest_from_dict(raw: Mapping[str, object]) -> UsbExportManifest:
    """Build a UsbExportManifest from parsed JSON."""
    completed_raw = raw.get("completed_at_utc")
    completed_at = _require_str(completed_raw, "completed_at_utc") if completed_raw else None
    playlists_raw = raw.get("playlist_names", [])
    if not isinstance(playlists_raw, list) or not all(
        isinstance(item, str) for item in playlists_raw
    ):
        raise ValueError("Manifest field 'playlist_names' must be a list of strings.")
    track_count_raw = raw.get("track_count")
    if isinstance(track_count_raw, bool) or not isinstance(track_count_raw, int):
        raise ValueError("Manifest field 'track_count' must be an integer.")
    return UsbExportManifest(
        export_id=_require_str(raw.get("export_id"), "export_id"),
        state=_require_str(raw.get("state"), "state"),
        started_at_utc=_require_str(raw.get("started_at_utc"), "started_at_utc"),
        completed_at_utc=completed_at,
        app_version=_require_str(raw.get("app_version"), "app_version"),
        adapter_versions=_parse_str_dict(raw.get("adapter_versions"), "adapter_versions"),
        analysis_versions=_parse_str_dict(raw.get("analysis_versions"), "analysis_versions"),
        readers=_parse_readers(raw.get("readers")),
        scope=ExportScope(_require_str(raw.get("scope"), "scope")),
        playlist_names=tuple(playlists_raw),
        track_count=track_count_raw,
        target_counts=_parse_target_counts(raw.get("target_counts")),
        omissions=_parse_omissions(raw.get("omissions")),
        my_tags_sha256=_require_str(raw.get("my_tags_sha256"), "my_tags_sha256"),
    )


def write_in_progress_manifest(mount_root: Path, manifest: UsbExportManifest) -> Path:
    """Write the temporary USB in-progress manifest while preserving completed data (D200)."""
    if manifest.state != MANIFEST_STATE_IN_PROGRESS:
        raise ValueError("In-progress manifest must have state 'in_progress'.")
    target = _confined_manifest_file(mount_root, IN_PROGRESS_MANIFEST_FILE_NAME)
    _write_json_atomic(target, _manifest_to_dict(manifest))
    return target


def write_completed_manifest(mount_root: Path, manifest: UsbExportManifest) -> Path:
    """Write the latest completed manifest and remove the temporary in-progress file (D140)."""
    if manifest.state != MANIFEST_STATE_COMPLETED:
        raise ValueError("Completed manifest must have state 'completed'.")
    if not manifest.completed_at_utc:
        raise ValueError("Completed manifest must include a UTC completion timestamp.")
    target = _confined_manifest_file(mount_root, COMPLETED_MANIFEST_FILE_NAME)
    _write_json_atomic(target, _manifest_to_dict(manifest))
    in_progress = _confined_manifest_file(mount_root, IN_PROGRESS_MANIFEST_FILE_NAME)
    in_progress.unlink(missing_ok=True)
    return target


def _read_manifest_file(mount_root: Path, file_name: str) -> UsbExportManifest | None:
    """Read one JSON manifest file below mount_root, returning None when absent."""
    target = _confined_manifest_file(mount_root, file_name)
    if not target.exists():
        return None
    if not target.is_file():
        return None
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"Manifest file {file_name} must hold a JSON object.")
    return _manifest_from_dict(raw)


def read_completed_manifest(mount_root: Path) -> UsbExportManifest | None:
    """Read the completed USB manifest if present."""
    return _read_manifest_file(mount_root, COMPLETED_MANIFEST_FILE_NAME)


def read_in_progress_manifest(mount_root: Path) -> UsbExportManifest | None:
    """Read the temporary in-progress USB manifest if present."""
    return _read_manifest_file(mount_root, IN_PROGRESS_MANIFEST_FILE_NAME)


def remove_in_progress_manifest(mount_root: Path) -> bool:
    """Remove the USB in-progress manifest during recovery while keeping history (D139)."""
    target = _confined_manifest_file(mount_root, IN_PROGRESS_MANIFEST_FILE_NAME)
    if not target.exists():
        return False
    target.unlink()
    return True
