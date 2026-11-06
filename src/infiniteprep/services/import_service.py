"""Import and inventory service (Section 7.A)."""

from __future__ import annotations

import sqlite3
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    MetadataTagAdapter,
    TechnicalAndTagMetadata,
)
from infiniteprep.db.repositories import Repository, RepositoryBatch
from infiniteprep.domain.enums import (
    AudioFormat,
    GroupingBasis,
    ReviewStatus,
    SourceType,
    TaskStatus,
)
from infiniteprep.domain.models import FileAsset, SourceGroup
from infiniteprep.hashing import compute_sha256

MULTITRACK_DURATION_THRESHOLD_SEC: Final[float] = 1200.0
MAX_DIRECTORY_DEPTH: Final[int] = 32
MAX_DISCOVERED_FILES: Final[int] = 10_000
MAX_IMPORT_FILE_BYTES: Final[int] = 2_147_483_648  # 2 GiB per file
# A file that fails with one of these skips alone. Any other error rolls back the batch.
PER_FILE_ERRORS: Final[tuple[type[Exception], ...]] = (OSError, sqlite3.Error)

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


class ImportOutcome(StrEnum):
    """Whether an import batch committed or rolled back."""

    COMMITTED = "committed"
    CANCELLED = "cancelled"


class ImportCancelledError(Exception):
    """Raised inside a batch to roll it back after a cancel request."""


@dataclass
class ImportBatchReport:
    """Summary returned after importing files or folders."""

    batch_id: str
    imported_files: list[FileAsset] = field(default_factory=list)
    previously_imported_paths: list[str] = field(default_factory=list)
    possible_duplicates: list[tuple[FileAsset, list[FileAsset]]] = field(default_factory=list)
    unsupported_files: list[str] = field(default_factory=list)
    damaged_files: list[tuple[str, str]] = field(default_factory=list)
    failed_files: list[tuple[str, str]] = field(default_factory=list)
    scanned_count: int = 0
    outcome: ImportOutcome = ImportOutcome.COMMITTED

    def mark_rolled_back(self) -> None:
        """Record a cancelled batch. Nothing it imported stays in the database."""
        self.outcome = ImportOutcome.CANCELLED
        self.imported_files.clear()
        self.damaged_files.clear()
        self.possible_duplicates.clear()


@dataclass
class _ImportRun:
    """State shared by every file of one import batch."""

    batch_id: str
    source_type: SourceType
    report: ImportBatchReport
    batch: RepositoryBatch


def _scan_single_directory_children(
    current_dir: Path,
    depth: int,
    discovered: list[Path],
    queue: deque[tuple[Path, int]],
    *,
    max_depth: int,
    max_files: int,
) -> None:
    """Append non-symlink child files to discovered and enqueue subdirectories within bounds."""
    for child in sorted(current_dir.iterdir()):
        if len(discovered) >= max_files:
            return
        if child.is_symlink():
            continue
        resolved_child = child.resolve()
        if resolved_child.is_file():
            discovered.append(resolved_child)
            continue
        if resolved_child.is_dir() and depth + 1 <= max_depth:
            queue.append((resolved_child, depth + 1))


def discover_files_bounded(
    paths: list[Path | str],
    max_depth: int = MAX_DIRECTORY_DEPTH,
    max_files: int = MAX_DISCOVERED_FILES,
) -> list[Path]:
    """Iteratively discover non-symlink files up to max_depth and max_files."""
    discovered: list[Path] = []
    visited_dirs: set[Path] = set()
    queue: deque[tuple[Path, int]] = deque()

    for raw_p in paths:
        if len(discovered) >= max_files:
            break
        candidate = Path(raw_p)
        if candidate.is_symlink():
            continue
        resolved = candidate.resolve()
        if resolved.is_file():
            discovered.append(resolved)
        elif resolved.is_dir():
            queue.append((resolved, 0))

    while queue and len(discovered) < max_files:
        current_dir, depth = queue.popleft()
        if current_dir in visited_dirs or depth > max_depth:
            continue
        visited_dirs.add(current_dir)
        _scan_single_directory_children(
            current_dir,
            depth,
            discovered,
            queue,
            max_depth=max_depth,
            max_files=max_files,
        )

    return discovered


class ImportService:
    """Non-destructive file and folder import, inventory, and folder-group inference."""

    def __init__(
        self, repository: Repository, tag_adapter: MetadataTagAdapter | None = None
    ) -> None:
        """Initialize ImportService with a Repository and MetadataTagAdapter."""
        self.repo = repository
        self.tag_adapter = tag_adapter or MetadataTagAdapter()

    def import_paths(
        self,
        paths: list[Path | str],
        source_type: SourceType = SourceType.UNKNOWN,
        *,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> ImportBatchReport:
        """Import files and folders non-destructively and return an ImportBatchReport.

        The scan runs first. Every file then imports inside one transaction, with a
        savepoint per file. progress receives (file number, total, path) before each
        file. A true should_cancel stops before the next file and rolls back the batch.
        """
        report = ImportBatchReport(batch_id=str(uuid.uuid4()))
        discovered = discover_files_bounded(paths)
        report.scanned_count = len(discovered)
        try:
            with self.repo.batch() as batch:
                run = _ImportRun(report.batch_id, source_type, report, batch)
                self._import_discovered(discovered, run, progress, should_cancel)
        except ImportCancelledError:
            report.mark_rolled_back()
        return report

    def _import_discovered(
        self,
        discovered: list[Path],
        run: _ImportRun,
        progress: ProgressCallback | None,
        should_cancel: CancelCheck | None,
    ) -> None:
        """Import each discovered file in order, checking for a cancel request first."""
        total = len(discovered)
        for index, file_path in enumerate(discovered):
            if should_cancel is not None and should_cancel():
                raise ImportCancelledError
            if progress is not None:
                progress(index + 1, total, str(file_path))
            self._import_in_savepoint(file_path, run)

    def _import_in_savepoint(self, file_path: Path, run: _ImportRun) -> None:
        """Import one file. A file or database error rolls back that file only."""
        try:
            with run.batch.savepoint():
                self._import_single_file(file_path, run.batch_id, run.source_type, run.report)
        except PER_FILE_ERRORS as exc:
            run.report.failed_files.append((str(file_path), f"{type(exc).__name__}: {exc}"))

    def _import_single_file(
        self,
        file_path: Path,
        batch_id: str,
        source_type: SourceType,
        report: ImportBatchReport,
    ) -> None:
        """Inspect and persist a single file without modifying it on disk."""
        path_str = str(file_path)
        if self.repo.get_file_by_path(path_str) is not None:
            report.previously_imported_paths.append(path_str)
            return

        file_size = file_path.stat().st_size
        if file_size > MAX_IMPORT_FILE_BYTES:
            report.unsupported_files.append(path_str)
            return

        fmt = self.tag_adapter.detect_format(file_path)
        if fmt == AudioFormat.UNSUPPORTED:
            report.unsupported_files.append(path_str)
            return

        content_hash = compute_sha256(file_path)
        prior_same_hash = self.repo.find_files_by_hash(content_hash)
        tech_meta = self.tag_adapter.read_metadata(file_path)
        has_damage = any("Damaged" in w or "Unrecognized" in w for w in tech_meta.warnings)
        source_group = self._ensure_folder_source_group(file_path.parent, source_type)

        is_multitrack = (
            tech_meta.duration is not None
            and tech_meta.duration >= MULTITRACK_DURATION_THRESHOLD_SEC
        )
        asset = FileAsset(
            id=str(uuid.uuid4()),
            current_path=path_str,
            original_path=path_str,
            file_size=file_size,
            content_hash=content_hash,
            format=fmt,
            duration=tech_meta.duration,
            original_tags=self._build_original_tags_payload(tech_meta),
            import_batch_id=batch_id,
            source_group_id=source_group.id,
            suspected_multitrack=is_multitrack,
            task_status=TaskStatus.IDLE,
            review_status=ReviewStatus.ERROR if has_damage else ReviewStatus.UNPROCESSED,
            error_message="; ".join(tech_meta.warnings) if has_damage else None,
        )
        self.repo.upsert_file_asset(asset)
        if has_damage:
            report.damaged_files.append((path_str, asset.error_message or "Damaged audio file"))
        else:
            report.imported_files.append(asset)
        if prior_same_hash:
            report.possible_duplicates.append((asset, prior_same_hash))

    def _build_original_tags_payload(self, tech_meta: TechnicalAndTagMetadata) -> dict[str, Any]:
        """Construct the structured original_tags JSON payload from technical metadata."""
        xdj_warnings = [w for w in tech_meta.warnings if w.startswith("XDJ-RX3 Warning")]
        return {
            "standard_tags": tech_meta.standard_tags,
            "raw_custom_tags": tech_meta.raw_custom_tags,
            "technical": {
                "sample_rate": tech_meta.sample_rate,
                "channels": tech_meta.channels,
                "bitrate": tech_meta.bitrate,
                "warnings": tech_meta.warnings,
                "xdj_rx3_warnings": xdj_warnings,
            },
        }

    def _ensure_folder_source_group(self, folder: Path, source_type: SourceType) -> SourceGroup:
        """Fetch or create a folder-hypothesis SourceGroup for a parent folder."""
        parent_str = str(folder)
        existing = self.repo.get_source_group_by_folder(parent_str)
        if existing is not None:
            return existing
        group = SourceGroup(
            id=str(uuid.uuid4()),
            user_label=folder.name or parent_str,
            grouping_basis=GroupingBasis.FOLDER_HYPOTHESIS,
            original_folder=parent_str,
            source_type=source_type,
            notes="Automatically grouped by shared parent folder upon import (hypothesis).",
        )
        return self.repo.upsert_source_group(group)
