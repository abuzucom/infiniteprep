"""The bounded search that finds a moved audio file (D333).

A source library names its audio files by path. A file that moved keeps its
name and its size, so the search looks for that pair inside the root the DJ
chose. A hash check confirms the candidate. The search never leaves the root and
never follows a symbolic link out of it.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from infiniteprep.hashing import HASH_CHUNK_BYTES
from infiniteprep.services.library_import.filesystem import (
    OpenedFile,
    identity_still_matches,
    is_link_or_junction,
    open_inside_root,
)

logger = logging.getLogger(__name__)

# The search walks the chosen root to this depth.
MAX_SEARCH_DEPTH: Final[int] = 4
# The search reads at most this many files of the root.
MAX_SEARCH_ENTRIES: Final[int] = 20_000
# The search visits at most this many folders of the root.
MAX_SEARCH_FOLDERS: Final[int] = 4_000
# The search hashes at most this many candidate files.
MAX_HASH_OPERATIONS: Final[int] = 2_000
# The search hashes at most this many bytes in total.
MAX_HASH_BYTES: Final[int] = 8 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class MissingReference:
    """One file the source library names that no longer sits at that path.

    file_size and content_hash hold what the app already knows about the file.
    A reference with no size cannot be matched by size, and a reference with no
    hash stays unconfirmed.
    """

    path: str
    file_size: int | None = None
    content_hash: str = ""


@dataclass(frozen=True)
class RelocationResult:
    """The outcome of the search for one missing file."""

    original_path: str
    found_path: Path | None
    confirmed: bool


def find_relocated_files(references: list[MissingReference], root: Path) -> list[RelocationResult]:
    """Look for each missing file inside the root and confirm it by hash.

    A reference without a known size stays unresolved, because a name alone
    would match too many files. The search never leaves the root. A root
    that is a link or that is not an absolute path bounds nothing, so the
    search refuses it.
    """
    if not references:
        return []
    search_root = _safe_root(root)
    if search_root is None:
        return [RelocationResult(item.path, None, False) for item in references]
    wanted = _references_by_name(references)
    found = _scan_for_candidates(search_root, wanted)
    hashes = _HashCache(search_root)
    return [_result_for(item, found, hashes) for item in references]


def _safe_root(root: Path) -> Path | None:
    """Return the resolved root folder, or None when it cannot bound a search."""
    if not root.is_absolute() or is_link_or_junction(root):
        return None
    try:
        resolved = root.resolve(strict=False)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _references_by_name(
    references: list[MissingReference],
) -> dict[str, list[MissingReference]]:
    """Return the references grouped by file name."""
    grouped: dict[str, list[MissingReference]] = {}
    for reference in references:
        grouped.setdefault(Path(reference.path).name, []).append(reference)
    return grouped


def _result_for(
    reference: MissingReference, found: dict[str, list[Path]], hashes: _HashCache
) -> RelocationResult:
    """Return the outcome for one missing reference."""
    name = Path(reference.path).name
    for candidate in found.get(name, []):
        if not _size_matches(reference, candidate):
            continue
        if _hash_confirms(reference, candidate, hashes):
            return RelocationResult(reference.path, candidate, True)
    return RelocationResult(reference.path, None, False)


def _size_matches(reference: MissingReference, candidate: Path) -> bool:
    """Return True when the candidate holds the recorded size of the file."""
    if reference.file_size is None:
        return False
    return _file_size(candidate) == reference.file_size


def _hash_confirms(reference: MissingReference, candidate: Path, hashes: _HashCache) -> bool:
    """Return True when the candidate holds the recorded content of the file."""
    if not reference.content_hash:
        return False
    digest = hashes.digest_of(candidate)
    if digest is None:
        return False
    return digest == reference.content_hash


class _HashCache:
    """The content hashes of the candidate files, read once each.

    A source library can name the same file many times. The cache keeps the
    hash of each candidate, so a file is read once however many references
    match it. The cache also bounds the work, so a root full of large files
    cannot make the search read without end.

    A candidate opens inside the root, so a file that a link replaced never
    sends the read outside the root. The identity of the file is checked
    after the read, so a file that changed while it was read never confirms.
    """

    def __init__(self, root: Path) -> None:
        """Create an empty cache for the search root."""
        self._root = root
        self._digests: dict[tuple[int, int, int, int], str] = {}
        self._operations = 0
        self._bytes = 0

    def digest_of(self, candidate: Path) -> str | None:
        """Return the content hash of a candidate, or None when it cannot be read."""
        opened = open_inside_root(candidate, self._root)
        if opened is None:
            return None
        with opened:
            stat_info = _opened_stat(opened)
            if stat_info is None:
                return None
            key = (
                opened.identity.device,
                opened.identity.inode,
                stat_info.st_size,
                stat_info.st_mtime_ns,
            )
            cached = self._digests.get(key)
            if cached is not None:
                return cached
            if not self._can_hash(stat_info.st_size):
                return None
            digest = _digest_of_handle(opened.handle)
            if not _handle_unchanged(opened, stat_info):
                logger.warning("Relocation skipped one file that changed while it was read.")
                return None
            self._digests[key] = digest
            self._operations += 1
            self._bytes += stat_info.st_size
            return digest

    def _can_hash(self, size: int) -> bool:
        """Return True when hashing size bytes stays within the work bounds."""
        if self._operations >= MAX_HASH_OPERATIONS or self._bytes >= MAX_HASH_BYTES:
            logger.warning("Relocation stopped hashing at the work bound.")
            return False
        return self._bytes + size <= MAX_HASH_BYTES


def _opened_stat(opened: OpenedFile) -> os.stat_result | None:
    """Return stat metadata of an open file, or None when it cannot be read."""
    try:
        return os.fstat(opened.handle.fileno())
    except OSError:
        logger.warning("Relocation skipped one file after an unreadable stat.")
        return None


def _handle_unchanged(opened: OpenedFile, before: os.stat_result) -> bool:
    """Return True when path identity, size, and mtime still match after reading."""
    if not identity_still_matches(opened):
        return False
    after = _opened_stat(opened)
    if after is None:
        return False
    return after.st_size == before.st_size and after.st_mtime_ns == before.st_mtime_ns


def _digest_of_handle(handle: IO[bytes]) -> str:
    """Return the content hash of an open file."""
    hasher = hashlib.sha256()
    try:
        handle.seek(0)
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            hasher.update(chunk)
    except OSError:
        logger.warning("Relocation skipped one file after an unreadable hash.")
        return ""
    return hasher.hexdigest()


def _scan_for_candidates(
    root: Path, wanted: dict[str, list[MissingReference]]
) -> dict[str, list[Path]]:
    """Return the files of the root that carry a wanted name."""
    candidates: dict[str, list[Path]] = {}
    for path in _walk_bounded(root):
        name = path.name
        if name not in wanted:
            continue
        candidates.setdefault(name, []).append(path)
    return candidates


def _walk_bounded(root: Path) -> list[Path]:
    """Return the files below the root, to a shallow depth and a bounded count.

    The search visits a bounded number of folders and reads a bounded number
    of files, so a root full of empty folders ends the search.
    """
    entries: list[Path] = []
    pending = [(root, 0)]
    visited = 0
    while pending and len(entries) < MAX_SEARCH_ENTRIES and visited < MAX_SEARCH_FOLDERS:
        current, depth = pending.pop(0)
        visited += 1
        folders, files = _split_children(current)
        entries.extend(files)
        entries = entries[:MAX_SEARCH_ENTRIES]
        if depth + 1 < MAX_SEARCH_DEPTH:
            pending.extend((folder, depth + 1) for folder in folders)
            pending = pending[:MAX_SEARCH_FOLDERS]
    return entries


def _split_children(folder: Path) -> tuple[list[Path], list[Path]]:
    """Return the real subfolders and the real files of a folder."""
    try:
        children = sorted(folder.iterdir())
    except OSError:
        logger.warning("Relocation skipped one unreadable folder.")
        return [], []
    real = [child for child in children if not is_link_or_junction(child)]
    return [child for child in real if child.is_dir()], [child for child in real if child.is_file()]


def _file_size(path: Path) -> int | None:
    """Return the size of the file, or None when it cannot be read.

    The size comes from the link itself, so a file that a link replaced never
    reports the size of the file the link points at.
    """
    try:
        return os.lstat(path).st_size
    except OSError:
        logger.warning("Relocation skipped one file after an unreadable stat.")
        return None
