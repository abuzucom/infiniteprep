"""Tests for journal-first tag writes, rollback on failure, and staged restore."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter, compute_sha256
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.services import write_service as write_service_module
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_wav_fixture


@pytest.fixture
def pending_write(tmp_path: Path) -> tuple[Repository, WriteService, FileWritePlan, Path, str]:
    """Import one tagged WAV, approve a new artist, and build its write plan."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MutagenAdapter()
    source = create_wav_fixture(
        tmp_path / "crate" / "track.wav", duration_sec=1.0, tags={"artist": "Old", "title": "T"}
    )
    original_hash = compute_sha256(source)
    asset = ImportService(repo, tag_adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")
    plan = write_svc.build_batch_preview([asset.id])[0]
    assert plan.is_safe_to_apply
    return repo, write_svc, plan, source, original_hash


def test_successful_write_leaves_one_completed_record(pending_write: Any) -> None:
    """The journal entry made before the write ends COMPLETED with both hashes."""
    repo, write_svc, plan, source, original_hash = pending_write

    record = write_svc.apply_single_plan(plan)

    records = repo.list_change_records(plan.file_id)
    assert [r.operation_id for r in records] == [record.operation_id]
    assert records[0].operation_status == OperationStatus.COMPLETED
    assert records[0].pre_write_hash == original_hash
    assert records[0].post_write_hash == compute_sha256(source)


def test_failure_after_backup_rolls_the_file_back(
    pending_write: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that corrupts the file and then fails restores the original bytes."""
    repo, write_svc, plan, source, original_hash = pending_write

    def corrupt_then_fail(path: Path, _tags: dict[str, str]) -> None:
        path.write_bytes(b"partial write")
        raise RuntimeError("simulated power loss")

    monkeypatch.setattr(write_svc.tag_adapter, "_write_tags_by_format", corrupt_then_fail)

    with pytest.raises(RuntimeError, match="simulated power loss"):
        write_svc.apply_single_plan(plan)

    assert compute_sha256(source) == original_hash
    record = repo.list_change_records(plan.file_id)[0]
    assert record.operation_status == OperationStatus.ROLLED_BACK
    assert "simulated power loss" in (record.error_message or "")


def test_failure_before_backup_marks_the_record_failed(
    pending_write: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed backup leaves the file untouched and records the failure."""
    repo, write_svc, plan, source, original_hash = pending_write

    def refuse_backup(*_args: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(write_svc.tag_adapter, "_create_verified_backup", refuse_backup)

    with pytest.raises(OSError, match="disk full"):
        write_svc.apply_single_plan(plan)

    assert compute_sha256(source) == original_hash
    record = repo.list_change_records(plan.file_id)[0]
    assert record.operation_status == OperationStatus.FAILED
    assert "disk full" in (record.error_message or "")


def test_unsafe_plan_creates_no_journal_record(pending_write: Any) -> None:
    """A plan that fails preflight raises before any journal entry exists."""
    repo, write_svc, plan, source, _ = pending_write
    source.write_bytes(source.read_bytes() + b"\x00\x00")
    tampered = write_svc.build_batch_preview([plan.file_id])[0]

    with pytest.raises(ValueError, match="preflight"):
        write_svc.apply_single_plan(tampered)

    assert repo.list_change_records(plan.file_id) == []


def test_restore_replaces_the_file_through_a_verified_staging_copy(pending_write: Any) -> None:
    """Restore returns the original bytes and leaves no staging file behind."""
    _, write_svc, plan, source, original_hash = pending_write
    record = write_svc.apply_single_plan(plan)

    restored = write_svc.restore_operation(record.operation_id)

    assert restored.operation_status == OperationStatus.RESTORED
    assert compute_sha256(source) == original_hash
    assert sorted(p.name for p in source.parent.iterdir()) == ["track.wav"]


def test_restore_failure_at_the_swap_keeps_the_current_file(
    pending_write: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed swap leaves the written file intact and removes the staging copy."""
    repo, write_svc, plan, source, _ = pending_write
    record = write_svc.apply_single_plan(plan)
    written_hash = compute_sha256(source)

    def refuse_replace(*_args: Any) -> None:
        raise OSError("device busy")

    monkeypatch.setattr(write_service_module.os, "replace", refuse_replace)

    with pytest.raises(OSError, match="device busy"):
        write_svc.restore_operation(record.operation_id)

    assert compute_sha256(source) == written_hash
    assert sorted(p.name for p in source.parent.iterdir()) == ["track.wav"]
    stored = repo.get_change_record(record.operation_id)
    assert stored is not None
    assert stored.operation_status == OperationStatus.COMPLETED
