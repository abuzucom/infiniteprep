"""Behavioral tests for queued write batches that continue past a failed file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.hashing import compute_sha256
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import PendingRecoveryError, WriteService
from tests.fixtures.audio_factory import create_wav_fixture

TRACK_NAMES = ("a_first.wav", "b_broken.wav", "c_last.wav")
BROKEN_NAME = "b_broken.wav"


@pytest.fixture
def write_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Repository, WriteService, list[FileWritePlan]]:
    """Build three safe write plans. The backup of the middle file fails."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MetadataTagAdapter()
    for name in TRACK_NAMES:
        create_wav_fixture(
            tmp_path / "crate" / name, duration_sec=0.5, tags={"artist": "Old", "title": name}
        )
    report = ImportService(repo, tag_adapter).import_paths([tmp_path / "crate"])
    by_name = {Path(asset.current_path).name: asset.id for asset in report.imported_files}
    file_ids = [by_name[name] for name in TRACK_NAMES]
    review = ReviewService(repo)
    for file_id in file_ids:
        review.set_manual_field_value(file_id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")
    plans = write_svc.build_batch_preview(file_ids)
    assert all(plan.is_safe_to_apply for plan in plans)
    make_backup = tag_adapter._create_verified_backup

    def refuse_broken_backup(source_path: Path, backup_path: Path, expected_hash: str) -> None:
        if source_path.name == BROKEN_NAME:
            raise OSError("disk full")
        make_backup(source_path, backup_path, expected_hash)

    monkeypatch.setattr(tag_adapter, "_create_verified_backup", refuse_broken_backup)
    return repo, write_svc, plans


def test_a_failed_write_lets_the_batch_continue_by_default(write_parts: Any) -> None:
    """The failed file is recorded and left untouched. The later file is still written."""
    repo, write_svc, plans = write_parts
    broken_hash = compute_sha256(plans[1].source_path)

    result = write_svc.apply_batch_continuing(plans)

    assert [record.file_id for record in result.records] == [plans[0].file_id, plans[2].file_id]
    assert len(result.failures) == 1
    failed_path, reason = result.failures[0]
    assert Path(failed_path).name == BROKEN_NAME
    assert "OSError: disk full" in reason
    assert result.stopped_early is False
    assert compute_sha256(plans[1].source_path) == broken_hash
    last_record = repo.list_change_records(plans[2].file_id)[0]
    assert last_record.operation_status == OperationStatus.COMPLETED


def test_records_of_one_batch_share_a_batch_operation_id(write_parts: Any) -> None:
    """Every write of a queued batch carries the same batch operation ID."""
    _repo, write_svc, plans = write_parts

    result = write_svc.apply_batch_continuing(plans)

    batch_ids = {record.batch_operation_id for record in result.records}
    assert len(batch_ids) == 1
    assert None not in batch_ids


def test_apply_batch_still_stops_at_the_first_failed_write(write_parts: Any) -> None:
    """The earlier method keeps its contract: the error propagates and later files wait."""
    repo, write_svc, plans = write_parts

    with pytest.raises(OSError, match="disk full"):
        write_svc.apply_batch(plans)

    assert repo.list_change_records(plans[2].file_id) == []


def test_stop_on_failure_ends_the_batch_at_the_first_failed_write(write_parts: Any) -> None:
    """With the stop rule on, files after the failed one stay unwritten."""
    repo, write_svc, plans = write_parts

    result = write_svc.apply_batch_continuing(plans, stop_on_failure=True)

    assert [record.file_id for record in result.records] == [plans[0].file_id]
    assert len(result.failures) == 1
    assert result.stopped_early is True
    assert repo.list_change_records(plans[2].file_id) == []


def test_cancel_stops_before_the_next_write_and_keeps_finished_writes(write_parts: Any) -> None:
    """A cancel request after the first file keeps that write and skips the rest."""
    repo, write_svc, plans = write_parts
    started: list[str] = []

    result = write_svc.apply_batch_continuing(
        plans,
        should_cancel=lambda: bool(started),
        progress=lambda _done, _total, path: started.append(Path(path).name),
    )

    assert started == [TRACK_NAMES[0]]
    assert [record.file_id for record in result.records] == [plans[0].file_id]
    assert result.failures == []
    assert result.stopped_early is True
    first_record = repo.list_change_records(plans[0].file_id)[0]
    assert first_record.operation_status == OperationStatus.COMPLETED
    assert repo.list_change_records(plans[1].file_id) == []


def test_pending_recovery_blocks_the_whole_batch_before_any_write(write_parts: Any) -> None:
    """An interrupted write still waiting for recovery refuses the batch up front."""
    repo, write_svc, plans = write_parts
    interrupted = write_svc._journal_in_progress(plans[0], None)
    assert interrupted.operation_status == OperationStatus.IN_PROGRESS

    with pytest.raises(PendingRecoveryError):
        write_svc.ensure_no_pending_recovery()
    with pytest.raises(PendingRecoveryError):
        write_svc.apply_batch_continuing(plans)

    assert repo.list_change_records(plans[2].file_id) == []
