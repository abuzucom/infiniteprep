"""Safe metadata write, rename, journal logging, crash recovery, and backup restore service."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import platformdirs

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter, compute_sha256
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import OperationStatus, ReviewStatus
from infiniteprep.domain.models import ChangeRecord
from infiniteprep.domain.ports import FileWritePlan


class WriteService:
    """Executes preflight checks, full backups, tag writes, renames, and verified restores."""

    def __init__(
        self,
        repository: Repository,
        tag_adapter: MutagenAdapter | None = None,
        backup_dir: Path | None = None,
        allowed_library_root: Path | None = None,
    ) -> None:
        """Initialize WriteService with Repository, MutagenAdapter, and backup directory."""
        self.repo = repository
        self.tag_adapter = tag_adapter or MutagenAdapter()
        if backup_dir is None:
            base = Path(platformdirs.user_data_dir("InfinitePrep", "InfinitePrep"))
            backup_dir = base / "backups"
        self.backup_dir = backup_dir
        self.allowed_library_root = allowed_library_root

    def build_batch_preview(
        self,
        file_ids: list[str],
        rename_template: str | None = None,
        enable_rename: bool = False,
    ) -> list[FileWritePlan]:
        """Build FileWritePlans for a batch of files and check cross-batch rename collisions."""
        plans: list[FileWritePlan] = []
        seen_destinations: set[Path] = set()
        for file_id in file_ids:
            asset = self.repo.get_file_by_id(file_id)
            if asset is None:
                continue
            approved = self.repo.get_approved_fields_map(file_id)
            plan = self.tag_adapter.preview(
                file_asset=asset,
                approved_fields=approved,
                backup_dir=self.backup_dir,
                rename_template=rename_template,
                enable_rename=enable_rename,
            )
            if plan.rename_enabled:
                if plan.destination_path in seen_destinations:
                    plan.collision_warnings.append(
                        f"Batch rename collision on destination '{plan.destination_path.name}'."
                    )
                seen_destinations.add(plan.destination_path)
            plans.append(plan)
        return plans

    def apply_batch(self, write_plans: list[FileWritePlan]) -> list[ChangeRecord]:
        """Apply all safe FileWritePlans, recording ChangeRecords and updating FileAssets."""
        batch_op_id = str(uuid.uuid4())
        records: list[ChangeRecord] = []
        for plan in write_plans:
            record = self.apply_single_plan(plan, batch_operation_id=batch_op_id)
            records.append(record)
        return records

    def apply_single_plan(
        self, plan: FileWritePlan, batch_operation_id: str | None = None
    ) -> ChangeRecord:
        """Execute a single FileWritePlan and persist its ChangeRecord and updated FileAsset."""
        op_id = str(uuid.uuid4())
        record = self.tag_adapter.apply(plan, operation_id=op_id)
        record.batch_operation_id = batch_operation_id
        self.repo.upsert_change_record(record)

        asset = self.repo.get_file_by_id(plan.file_id)
        if asset is not None:
            asset.current_path = record.new_path
            asset.content_hash = record.post_write_hash or compute_sha256(Path(record.new_path))
            asset.review_status = ReviewStatus.WRITTEN
            std = dict(asset.original_tags.get("standard_tags", {}))
            std.update(plan.proposed_tags_to_write)
            asset.original_tags["standard_tags"] = std
            self.repo.upsert_file_asset(asset)
        return record

    def restore_operation(
        self,
        operation_id: str,
        *,
        allowed_library_root: Path | None = None,
    ) -> ChangeRecord:
        """Restore a file from its backup_reference and verify SHA-256 integrity."""
        record = self.repo.get_change_record(operation_id)
        if record is None:
            raise ValueError(f"ChangeRecord {operation_id} not found.")
        backup_path = self._verify_backup_file(record, operation_id)
        prev_path, cur_path = self._resolve_and_verify_restore_paths(
            record, allowed_library_root=allowed_library_root
        )
        prev_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, prev_path)
        if cur_path != prev_path and cur_path.exists():
            cur_path.unlink()

        restored_hash = compute_sha256(prev_path)
        record.operation_status = OperationStatus.RESTORED
        self.repo.upsert_change_record(record)

        asset = self.repo.get_file_by_id(record.file_id)
        if asset is not None:
            asset.current_path = str(prev_path)
            asset.content_hash = restored_hash
            asset.review_status = ReviewStatus.APPROVED
            asset.original_tags["standard_tags"] = dict(record.previous_tags)
            self.repo.upsert_file_asset(asset)
        return record

    def _verify_backup_file(self, record: ChangeRecord, operation_id: str) -> Path:
        """Verify backup_reference is within backup_dir, exists, and matches pre_write_hash."""
        backup_path = Path(record.backup_reference).resolve()
        allowed_backup_root = self.backup_dir.resolve()
        if not backup_path.is_relative_to(allowed_backup_root):
            raise ValueError(
                f"Backup path '{backup_path}' escapes backup directory '{allowed_backup_root}'."
            )
        if not backup_path.exists():
            raise FileNotFoundError(
                f"Backup file missing at {backup_path}; cannot restore operation {operation_id}."
            )
        if record.pre_write_hash and compute_sha256(backup_path) != record.pre_write_hash:
            raise ValueError("Backup file SHA-256 mismatch; backup may be corrupted.")
        return backup_path

    def _resolve_and_verify_restore_paths(
        self,
        record: ChangeRecord,
        *,
        allowed_library_root: Path | None = None,
    ) -> tuple[Path, Path]:
        """Resolve previous_path and new_path and verify both stay within library root."""
        raw_prev = Path(record.previous_path)
        raw_cur = Path(record.new_path)
        if ".." in raw_prev.parts or ".." in raw_cur.parts:
            raise ValueError("Path traversal '..' is not permitted in ChangeRecord restore paths.")
        prev_path = raw_prev.resolve()
        cur_path = raw_cur.resolve()
        asset = self.repo.get_file_by_id(record.file_id)
        default_root = (
            Path(asset.original_path).resolve().parent if asset is not None else prev_path.parent
        )
        effective_root = (
            allowed_library_root or self.allowed_library_root or default_root
        ).resolve()
        if not prev_path.is_relative_to(effective_root) or not cur_path.is_relative_to(
            effective_root
        ):
            raise ValueError(
                f"Restore paths '{prev_path}' / '{cur_path}' escape allowed library root "
                f"'{effective_root}'."
            )
        return prev_path, cur_path
