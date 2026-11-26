"""Tests for failed rollbacks, unreadable backups, and finished writes in recovery."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    MetadataTagAdapter,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.hashing import compute_sha256
from infiniteprep.services import write_service as write_service_module
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import PendingRecoveryError, WriteService
from tests.fixtures.audio_factory import create_wav_fixture


@pytest.fixture
def library(tmp_path: Path) -> tuple[Repository, WriteService, str, Path]:
    """Import one tagged WAV and approve a new artist on it."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MetadataTagAdapter()
    source = create_wav_fixture(
        tmp_path / "crate" / "track.wav", duration_sec=1.0, tags={"artist": "Old", "title": "T"}
    )
    asset = ImportService(repo, tag_adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")
    return repo, write_svc, asset.id, source


def fail_write_after_corrupting(write_svc: WriteService, monkeypatch: Any) -> None:
    """Make the tag write corrupt the file and then raise OSError."""

    def corrupt_then_fail(path: Path, _tags: dict[str, str]) -> None:
        path.write_bytes(b"partial write")
        raise OSError("write failed")

    monkeypatch.setattr(write_svc.tag_adapter, "_write_tags_by_format", corrupt_then_fail)


def test_failed_rollback_stays_in_recovery(library: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The half-written file stays listed for recovery, and new writes stay blocked."""
    _, write_svc, file_id, _ = library
    fail_write_after_corrupting(write_svc, monkeypatch)

    def refuse_rollback(*_args: Any) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(write_svc, "_restore_into_place", refuse_rollback)
    plan = write_svc.build_batch_preview([file_id])[0]

    with pytest.raises(OSError, match="write failed"):
        write_svc.apply_single_plan(plan)

    pending = write_svc.list_interrupted_operations()
    assert len(pending) == 1
    assert "rollback failed (disk full)" in (pending[0].error_message or "")
    with pytest.raises(PendingRecoveryError):
        write_svc.apply_batch([plan])


def test_unreadable_backup_keeps_original_error(
    library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backup read error during rollback cannot replace the write error."""
    repo, write_svc, file_id, _ = library
    fail_write_after_corrupting(write_svc, monkeypatch)

    def unreadable_backup(_path: Path) -> str:
        raise PermissionError("backup unreadable")

    monkeypatch.setattr(write_service_module, "compute_sha256", unreadable_backup)
    plan = write_svc.build_batch_preview([file_id])[0]

    with pytest.raises(OSError, match="write failed"):
        write_svc.apply_single_plan(plan)

    record = repo.list_change_records(file_id)[0]
    assert record.operation_status == OperationStatus.IN_PROGRESS
    assert "backup unreadable" in (record.error_message or "")


def test_mark_finished_clears_preflight(library: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """After Mark as finished, the library holds the file's current path and hash."""
    repo, write_svc, file_id, source = library
    imported_hash = compute_sha256(source)

    # The tag writer only sees the staged copy, which the adapter discards. The fake
    # changes the source itself, so Mark as finished has new bytes to record.
    def corrupt_then_die(_staged: Path, _tags: dict[str, str]) -> None:
        source.write_bytes(source.read_bytes() + b"\x00")
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(write_svc.tag_adapter, "_write_tags_by_format", corrupt_then_die)
        with pytest.raises(KeyboardInterrupt):
            write_svc.apply_single_plan(write_svc.build_batch_preview([file_id])[0])
    operation_id = write_svc.list_interrupted_operations()[0].operation_id

    write_svc.mark_interrupted_finished(operation_id)

    asset = repo.get_file_by_id(file_id)
    assert asset is not None
    assert compute_sha256(source) != imported_hash
    assert asset.content_hash == compute_sha256(source)
    assert write_svc.build_batch_preview([file_id])[0].is_safe_to_apply
