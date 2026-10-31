"""Safe metadata write, rename, journal logging, crash recovery, and backup restore service."""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import platformdirs

from infiniteprep.adapters.metadata_io.mutagen_adapter import (
    MutagenAdapter,
    compute_sha256,
    operation_backup_path,
)
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import OperationStatus, ReviewStatus
from infiniteprep.domain.models import ChangeRecord
from infiniteprep.domain.ports import FileWritePlan

logger = logging.getLogger(__name__)


class PendingRecoveryError(RuntimeError):
    """Raised when a write starts while an interrupted write still waits for recovery."""


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

    def list_interrupted_operations(self) -> list[ChangeRecord]:
        """Return journal records left IN_PROGRESS by a crash or power loss mid-write."""
        return [
            record
            for record in self.repo.list_change_records()
            if record.operation_status == OperationStatus.IN_PROGRESS
        ]

    def _ensure_no_pending_recovery(self) -> None:
        """Refuse a new write while an interrupted write may have left a half-written file."""
        pending = self.list_interrupted_operations()
        if pending:
            raise PendingRecoveryError(
                f"{len(pending)} interrupted write(s) wait for recovery. Restore or mark each "
                "one as finished in the startup recovery dialog, then retry."
            )

    def restore_interrupted(self, operation_id: str) -> ChangeRecord:
        """Restore an interrupted write from its verified backup and mark it ROLLED_BACK."""
        record = self._get_interrupted(operation_id)
        return self._restore_record(record, OperationStatus.ROLLED_BACK)

    def mark_interrupted_finished(self, operation_id: str) -> ChangeRecord:
        """Accept the file on disk for an interrupted write and mark its record COMPLETED."""
        record = self._get_interrupted(operation_id)
        # The chosen path becomes the asset's write target. Both stored paths must stay in the
        # library root.
        prev_path, cur_path = self._resolve_and_verify_restore_paths(record)
        current = cur_path if cur_path.is_file() else prev_path
        record.post_write_hash = compute_sha256(current)
        record.new_path = str(current)
        record.operation_status = OperationStatus.COMPLETED
        self.repo.upsert_change_record(record)
        # The next write preflights against the stored path and hash, so match the file on disk.
        asset = self.repo.get_file_by_id(record.file_id)
        if asset is not None:
            asset.current_path = str(current)
            asset.content_hash = record.post_write_hash
            self.repo.upsert_file_asset(asset)
        return record

    def _get_interrupted(self, operation_id: str) -> ChangeRecord:
        """Return the IN_PROGRESS record for operation_id, or raise ValueError."""
        record = self.repo.get_change_record(operation_id)
        if record is None or record.operation_status != OperationStatus.IN_PROGRESS:
            raise ValueError(f"ChangeRecord {operation_id} is not an interrupted write.")
        return record

    def apply_batch(self, write_plans: list[FileWritePlan]) -> list[ChangeRecord]:
        """Apply all safe FileWritePlans, recording ChangeRecords and updating FileAssets."""
        self._ensure_no_pending_recovery()
        batch_op_id = str(uuid.uuid4())
        records: list[ChangeRecord] = []
        for plan in write_plans:
            record = self.apply_single_plan(plan, batch_operation_id=batch_op_id)
            records.append(record)
        return records

    def apply_single_plan(
        self, plan: FileWritePlan, batch_operation_id: str | None = None
    ) -> ChangeRecord:
        """Journal the plan as IN_PROGRESS, apply it, then complete the record or roll back."""
        self._ensure_no_pending_recovery()
        if not plan.is_safe_to_apply:
            # The adapter raises the preflight error before it touches the file.
            return self.tag_adapter.apply(plan, operation_id=str(uuid.uuid4()))
        pending = self._journal_in_progress(plan, batch_operation_id)
        try:
            record = self.tag_adapter.apply(
                plan,
                operation_id=pending.operation_id,
                backup_path=Path(pending.backup_reference),
            )
        except Exception as exc:
            # Any failure after the journal entry may leave a partial write. Roll back, re-raise.
            self._roll_back_failed_write(pending, exc)
            raise
        record.batch_operation_id = batch_operation_id
        self.repo.upsert_change_record(record)
        self._update_asset_after_write(plan, record)
        return record

    def _journal_in_progress(
        self, plan: FileWritePlan, batch_operation_id: str | None
    ) -> ChangeRecord:
        """Persist an IN_PROGRESS ChangeRecord that names the backup before any file change."""
        operation_id = str(uuid.uuid4())
        # apply receives this path, so the journal always names the backup the adapter writes.
        backup_path = operation_backup_path(
            plan.backup_destination_path.parent, plan.file_id, operation_id, plan.source_path.name
        )
        pending = ChangeRecord(
            operation_id=operation_id,
            file_id=plan.file_id,
            previous_path=str(plan.source_path),
            new_path=str(plan.destination_path),
            previous_tags=plan.existing_tags,
            written_tags=plan.proposed_tags_to_write,
            backup_reference=str(backup_path),
            operation_status=OperationStatus.IN_PROGRESS,
            timestamp=datetime.now(UTC).isoformat(),
            batch_operation_id=batch_operation_id,
            pre_write_hash=plan.expected_content_hash,
        )
        self.repo.upsert_change_record(pending)
        return pending

    def _roll_back_failed_write(self, pending: ChangeRecord, failure: Exception) -> None:
        """Record a failed write as FAILED or ROLLED_BACK, or keep it pending for recovery."""
        pending.error_message = f"{failure.__class__.__name__}: {failure}"
        try:
            pending.operation_status = self._restore_after_failure(pending)
        except Exception as rollback_exc:
            # The file may be half-written. IN_PROGRESS keeps it in startup recovery and blocks
            # new writes. The caller still re-raises the original failure.
            logger.error("Rollback of %s failed: %r", pending.operation_id, rollback_exc)
            pending.error_message += f"; rollback failed ({rollback_exc})"
            pending.operation_status = OperationStatus.IN_PROGRESS
        self.repo.upsert_change_record(pending)

    def _restore_after_failure(self, pending: ChangeRecord) -> OperationStatus:
        """Swap the verified backup back in, or report FAILED when no backup was taken."""
        backup_path = Path(pending.backup_reference)
        if not backup_path.is_file() or compute_sha256(backup_path) != pending.pre_write_hash:
            # The adapter verifies the backup before it writes, so the file is still untouched.
            return OperationStatus.FAILED
        self._restore_into_place(backup_path, pending)
        return OperationStatus.ROLLED_BACK

    def _restore_into_place(self, backup_path: Path, record: ChangeRecord) -> str:
        """Swap the backup into previous_path through a verified staging copy, then clean up."""
        prev_path = Path(record.previous_path)
        cur_path = Path(record.new_path)
        restored_hash = self._replace_with_staged_copy(
            backup_path, prev_path, record.pre_write_hash
        )
        if cur_path.resolve() != prev_path.resolve() and cur_path.exists():
            cur_path.unlink()
        return restored_hash

    def _replace_with_staged_copy(
        self, source: Path, target: Path, expected_hash: str | None
    ) -> str:
        """Copy source beside target, verify its SHA-256, then atomically replace target."""
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".{target.name}.restore_{uuid.uuid4().hex}")
        try:
            shutil.copy2(source, staging)
            staged_hash = compute_sha256(staging)
            if expected_hash and staged_hash != expected_hash:
                raise ValueError(
                    "Staged restore copy failed SHA-256 verification; the current file is "
                    "unchanged. Check the backup disk and retry."
                )
            os.replace(staging, target)
        finally:
            staging.unlink(missing_ok=True)
        return staged_hash

    def _update_asset_after_write(self, plan: FileWritePlan, record: ChangeRecord) -> None:
        """Point the FileAsset at the written file and merge the written tags."""
        asset = self.repo.get_file_by_id(plan.file_id)
        if asset is not None:
            asset.current_path = record.new_path
            asset.content_hash = record.post_write_hash or compute_sha256(Path(record.new_path))
            asset.review_status = ReviewStatus.WRITTEN
            std = dict(asset.original_tags.get("standard_tags", {}))
            std.update(plan.proposed_tags_to_write)
            asset.original_tags["standard_tags"] = std
            self.repo.upsert_file_asset(asset)

    def restore_operation(
        self,
        operation_id: str,
        *,
        allowed_library_root: Path | None = None,
    ) -> ChangeRecord:
        """Restore a file from its backup_reference through a SHA-256 verified staging copy."""
        record = self.repo.get_change_record(operation_id)
        if record is None:
            raise ValueError(f"ChangeRecord {operation_id} not found.")
        return self._restore_record(
            record, OperationStatus.RESTORED, allowed_library_root=allowed_library_root
        )

    def _restore_record(
        self,
        record: ChangeRecord,
        final_status: OperationStatus,
        *,
        allowed_library_root: Path | None = None,
    ) -> ChangeRecord:
        """Swap the verified backup in for the record, then store final_status and the asset."""
        backup_path = self._verify_backup_file(record, record.operation_id)
        prev_path, cur_path = self._resolve_and_verify_restore_paths(
            record, allowed_library_root=allowed_library_root
        )
        restored_hash = self._replace_with_staged_copy(
            backup_path, prev_path, record.pre_write_hash
        )
        if cur_path != prev_path and cur_path.exists():
            cur_path.unlink()

        record.operation_status = final_status
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
