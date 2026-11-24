"""The shared step that finds the DJ libraries installed on this computer.

The step lists what it finds. The DJ picks one. The step opens no file other
than the candidate library file, and it reads only a header of that file. It
never opens an audio file and never writes anything. The same step serves the
importers, the exporters, and the conversion pass of EXPORT-7.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from infiniteprep.services.library_import.filesystem import is_link_or_junction, real_children
from infiniteprep.services.library_import.locations import (
    FormatId,
    KnownLocation,
    Platform,
    folders_for,
    locations_for,
)
from infiniteprep.services.usb.targets import TargetCandidate, load_removable_targets

logger = logging.getLogger(__name__)

# The scan never leaves the standard parent folders of the user profile.
MAX_SCAN_DEPTH: Final[int] = 3
MAX_SCAN_ENTRIES: Final[int] = 4_000
# The header check reads this many bytes at the start of a candidate file.
HEADER_READ_BYTES: Final[int] = 4_096
# The folders the scan walks below the user profile folder.
SCAN_FOLDER_NAMES: Final[tuple[str, ...]] = (
    "Documents",
    "Music",
    "Library",
    "AppData/Roaming",
    "AppData/Local",
    ".mixxx",
    ".var/app/org.mixxx.Mixxx",
)
# A version attribute in the header of a library file.
VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(r'version\s*=\s*"([^"]{1,32})"', re.IGNORECASE)


@dataclass(frozen=True)
class LocatedLibrary:
    """One DJ library the locator found."""

    format_id: FormatId
    file_path: Path
    folder_path: Path
    version: str | None
    modified_at: float | None
    observed: bool
    citation_url: str
    citation_note: str


def find_installed_libraries(
    profile_folder: Path,
    platform: Platform,
    *,
    drives: tuple[TargetCandidate, ...] | None = None,
) -> list[LocatedLibrary]:
    """List the DJ libraries installed on this computer.

    The step checks the known folders, then walks the standard parent folders
    to a shallow depth, then reads the mounted drives. The drive list comes
    from the USB inspection layer.
    """
    found: list[LocatedLibrary] = []
    known = locations_for(platform)
    fixed = tuple(location for location in known if not location.on_device)
    for location in fixed:
        found.extend(_known_folder_matches(location, profile_folder, platform))
    found.extend(_scanned_matches(profile_folder, fixed))
    candidates = drives if drives is not None else load_removable_targets()
    found.extend(_drive_matches(candidates, known))
    return _without_duplicates(found)


def _without_duplicates(found: list[LocatedLibrary]) -> list[LocatedLibrary]:
    """Return the libraries with one entry per format and file."""
    unique: list[LocatedLibrary] = []
    seen: set[tuple[FormatId, Path]] = set()
    for library in found:
        key = (library.format_id, library.file_path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(library)
    return unique


def _known_folder_matches(
    location: KnownLocation, profile_folder: Path, platform: Platform
) -> list[LocatedLibrary]:
    """Return the libraries the known folders of this platform hold.

    A known folder that is a link, or that resolves outside the profile
    folder, holds no candidate.
    """
    matches: list[LocatedLibrary] = []
    for folder in folders_for(location, platform):
        parent = profile_folder / folder
        if not _folder_inside_profile(parent, profile_folder):
            continue
        matches.extend(_location_matches(location, parent, profile_folder))
    return matches


def _folder_inside_profile(folder: Path, profile_folder: Path) -> bool:
    """Return True when a folder of the profile holds candidates.

    A folder that is a link, or that resolves outside the profile folder,
    holds nothing the framework reads.
    """
    if is_link_or_junction(folder):
        logger.warning("Library import skipped a known folder that is a link.")
        return False
    try:
        resolved_folder = folder.resolve(strict=False)
        resolved_profile = profile_folder.resolve(strict=False)
    except OSError:
        return False
    try:
        resolved_folder.relative_to(resolved_profile)
    except ValueError:
        logger.warning("Library import skipped a known folder outside the profile.")
        return False
    return True


def _location_matches(
    location: KnownLocation, parent: Path, profile_folder: Path | None
) -> list[LocatedLibrary]:
    """Return the libraries of one known folder."""
    if not parent.is_dir():
        return []
    if location.versioned_parent:
        matches: list[LocatedLibrary] = []
        for candidate in _child_folders(parent):
            matches.extend(_library_in_folder(location, candidate, profile_folder))
        return matches
    return _library_in_folder(location, parent, profile_folder)


def _child_folders(parent: Path) -> list[Path]:
    """Return the real folders of a parent folder."""
    return real_children(parent)


def _library_in_folder(
    location: KnownLocation, folder: Path, profile_folder: Path | None = None
) -> list[LocatedLibrary]:
    """Return the library files of one format inside one folder.

    The step reads no file. The version comes from the folder name, which
    the Traktor Root Directory carries. The candidate is checked against the
    profile folder at the moment the step looks at it, so a folder that a
    link replaced after the walk holds no candidate.
    """
    file_path = folder / location.file_name
    if not file_path.is_file() or is_link_or_junction(file_path):
        return []
    if not _candidate_inside_profile(folder, file_path, profile_folder):
        logger.warning("Library import skipped a candidate outside the profile.")
        return []
    version = _folder_version(folder) if location.versioned_parent else None
    return [
        LocatedLibrary(
            format_id=location.format_id,
            file_path=file_path,
            folder_path=folder,
            version=version,
            modified_at=_modified_time(file_path),
            observed=location.observed,
            citation_url=location.citation_url,
            citation_note=location.citation_note,
        )
    ]


def _candidate_inside_profile(folder: Path, file_path: Path, profile_folder: Path | None) -> bool:
    """Return True when the folder and the candidate stay inside the profile.

    The resolved paths settle the question. A folder that a link replaced
    resolves outside the profile folder, so the candidate holds nothing the
    framework reads. A profile folder of None means the candidate sits on a
    mounted device, where the profile does not apply.
    """
    if profile_folder is None:
        return True
    if is_link_or_junction(folder) or is_link_or_junction(file_path):
        return False
    try:
        resolved_folder = folder.resolve(strict=False)
        resolved_file = file_path.resolve(strict=False)
        resolved_profile = profile_folder.resolve(strict=False)
    except OSError:
        return False
    for resolved in (resolved_folder, resolved_file):
        try:
            resolved.relative_to(resolved_profile)
        except ValueError:
            return False
    return True


def _folder_version(folder: Path) -> str | None:
    """Return the version the folder name carries, such as the one in Traktor 3.5.1."""
    name = folder.name
    if " " not in name:
        return None
    candidate = name.rsplit(" ", 1)[-1]
    return candidate if candidate and candidate[0].isdigit() else None


def read_library_version(file_path: Path) -> str | None:
    """Return the version the header of a chosen library file states, or None.

    The caller runs this after the DJ picks a library. The discovery step
    reads no file.
    """
    try:
        with file_path.open("rb") as handle:
            header = handle.read(HEADER_READ_BYTES)
    except OSError:
        logger.warning("Locator skipped one file after an unreadable header.")
        return None
    match = VERSION_PATTERN.search(header.decode("utf-8", errors="replace"))
    return match.group(1) if match else None


def _modified_time(file_path: Path) -> float | None:
    """Return the last-modified time of the file, or None when it cannot be read."""
    try:
        return file_path.stat().st_mtime
    except OSError:
        logger.warning("Locator skipped one file after an unreadable stat.")
        return None


def _scanned_matches(
    profile_folder: Path, known: tuple[KnownLocation, ...]
) -> list[LocatedLibrary]:
    """Return the libraries the standard parent folders hold."""
    if not profile_folder.is_dir():
        return []
    matches: list[LocatedLibrary] = []
    visited: set[Path] = set()
    for name in SCAN_FOLDER_NAMES:
        scan_root = _scan_root_inside_profile(profile_folder, name)
        if scan_root is None:
            continue
        for folder in _walk_bounded(scan_root):
            if folder in visited:
                continue
            visited.add(folder)
            matches.extend(_folder_matches(folder, known, profile_folder))
    return matches


def _scan_root_inside_profile(profile_folder: Path, name: str) -> Path | None:
    """Return the scan root when it stays inside the profile folder.

    A folder of the profile that is a link, or that resolves outside the
    profile, holds no candidate.
    """
    root = profile_folder / name
    if is_link_or_junction(root):
        logger.warning("Library import skipped a scan root that is a link.")
        return None
    try:
        resolved_root = root.resolve(strict=False)
        resolved_profile = profile_folder.resolve(strict=False)
    except OSError:
        return None
    try:
        resolved_root.relative_to(resolved_profile)
    except ValueError:
        logger.warning("Library import skipped a scan root outside the profile.")
        return None
    return root


def _folder_matches(
    folder: Path, known: tuple[KnownLocation, ...], profile_folder: Path
) -> list[LocatedLibrary]:
    """Return the libraries of one scanned folder."""
    matches: list[LocatedLibrary] = []
    for location in known:
        matches.extend(_library_in_folder(location, folder, profile_folder))
        if not location.versioned_parent:
            continue
        for candidate in _child_folders(folder):
            matches.extend(_library_in_folder(location, candidate, profile_folder))
    return matches


def _walk_bounded(root: Path) -> list[Path]:
    """Return the folders below the root, to a shallow depth and a bounded count.

    A root that is a link is refused, so the scan never leaves the profile
    through a folder that points elsewhere.
    """
    if is_link_or_junction(root):
        logger.warning("Library import skipped a scan root that is a link.")
        return []
    folders: list[Path] = []
    pending = [(root, 0)]
    while pending and len(folders) < MAX_SCAN_ENTRIES:
        current, depth = pending.pop(0)
        children = _child_folders(current)
        folders.extend(children[: MAX_SCAN_ENTRIES - len(folders)])
        if depth + 1 < MAX_SCAN_DEPTH:
            pending.extend((child, depth + 1) for child in children)
    return folders


def _drive_matches(
    drives: tuple[TargetCandidate, ...], known: tuple[KnownLocation, ...]
) -> list[LocatedLibrary]:
    """Return the libraries the mounted drives hold."""
    locations = tuple(location for location in known if location.on_device)
    if not locations:
        return []
    matches: list[LocatedLibrary] = []
    for candidate in drives:
        matches.extend(_drive_candidate_matches(locations, candidate))
    return matches


def _drive_candidate_matches(
    locations: tuple[KnownLocation, ...], candidate: TargetCandidate
) -> list[LocatedLibrary]:
    """Return the library folders of one mounted drive."""
    inspection = candidate.inspection
    if inspection is None:
        return []
    matches: list[LocatedLibrary] = []
    for partition in inspection.partitions:
        for mount_point in partition.mount_points:
            matches.extend(_mount_matches(locations, Path(mount_point)))
    return matches


def _mount_matches(locations: tuple[KnownLocation, ...], mount_point: Path) -> list[LocatedLibrary]:
    """Return the library folders of one mounted device."""
    matches: list[LocatedLibrary] = []
    for location in locations:
        for folder in folders_for(location, Platform.ANY):
            matches.extend(_library_in_folder(location, mount_point / folder))
    return matches
