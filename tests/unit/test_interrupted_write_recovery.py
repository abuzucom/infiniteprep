"""Tests for listing, restoring, and accepting writes a crash left IN_PROGRESS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter, compute_sha256
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import PendingRecoveryError, WriteService
from tests.fixtures.audio_factory import create_wav_fixture

CORRUPT_BYTES = b"half-written by a crash"


@pytest.fixture
def library(tmp_path: Path) -> tuple[Repository, WriteService, list[str], Path, str]:
    """Import two tagged WAVs and approve a new artist on each."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MutagenAdapter()
    paths = [
        create_wav_fixture(
            tmp_path / "crate" / f"track_{index}.wav",
            duration_sec=1.0,
            tags={"artist": "Old", "title": f"T{index}"},
        )
        for index in range(2)
    ]
    report = ImportService(repo, tag_adapter).import_paths(paths)
    by_path = {Path(a.current_path).resolve(): a.id for a in report.imported_files}
    file_ids = [by_path[path.resolve()] for path in paths]
    for file_id in file_ids:
        ReviewService(repo).set_manual_field_value(file_id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")
    return repo, write_svc, file_ids, paths[0], compute_sha256(paths[0])


def crash_mid_write(write_svc: WriteService, file_id: str, monkeypatch: Any) -> str:
    """Start a real write, corrupt the file, and stop it the way a process kill would."""

    def corrupt_then_die(path: Path, _tags: dict[str, str]) -> None:
        path.write_bytes(CORRUPT_BYTES)
        raise KeyboardInterrupt

    monkeypatch.setattr(write_svc.tag_adapter, "_write_tags_by_format", corrupt_then_die)
    plan = write_svc.build_batch_preview([file_id])[0]
    with pytest.raises(KeyboardInterrupt):
        write_svc.apply_single_plan(plan)
    monkeypatch.undo()
    return write_svc.list_interrupted_operations()[0].operation_id


def test_a_crash_mid_write_leaves_one_interrupted_record(
    library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the IN_PROGRESS record is listed, and the corrupted file is still on disk."""
    _, write_svc, file_ids, source, _ = library

    operation_id = crash_mid_write(write_svc, file_ids[0], monkeypatch)

    assert [r.operation_id for r in write_svc.list_interrupted_operations()] == [operation_id]
    assert compute_sha256(source) == library[4]
    assert sorted(p.name for p in source.parent.iterdir()) == ["track_0.wav", "track_1.wav"]


def test_restore_returns_the_original_and_marks_rolled_back(
    library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore puts back the verified backup and clears the interrupted list."""
    repo, write_svc, file_ids, source, original_hash = library
    operation_id = crash_mid_write(write_svc, file_ids[0], monkeypatch)

    write_svc.restore_interrupted(operation_id)

    assert compute_sha256(source) == original_hash
    stored = repo.get_change_record(operation_id)
    assert stored is not None
    assert stored.operation_status == OperationStatus.ROLLED_BACK
    assert write_svc.list_interrupted_operations() == []


def test_restore_with_a_tampered_backup_keeps_the_record_pending(
    library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backup that fails its hash check raises, and the write stays interrupted."""
    repo, write_svc, file_ids, source, _ = library
    operation_id = crash_mid_write(write_svc, file_ids[0], monkeypatch)
    record = repo.get_change_record(operation_id)
    assert record is not None
    Path(record.backup_reference).write_bytes(b"tampered")

    with pytest.raises(ValueError, match="SHA-256"):
        write_svc.restore_interrupted(operation_id)

    assert compute_sha256(source) == library[4]
    assert [r.operation_id for r in write_svc.list_interrupted_operations()] == [operation_id]


def test_mark_finished_keeps_the_file_and_records_its_hash(
    library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mark as finished stores COMPLETED and the hash of the file as it is now."""
    repo, write_svc, file_ids, source, _ = library
    operation_id = crash_mid_write(write_svc, file_ids[0], monkeypatch)

    write_svc.mark_interrupted_finished(operation_id)

    stored = repo.get_change_record(operation_id)
    assert stored is not None
    assert stored.operation_status == OperationStatus.COMPLETED
    assert stored.post_write_hash == compute_sha256(source)
    assert compute_sha256(source) == library[4]


def test_writes_are_blocked_while_a_record_is_pending(
    library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending record stops apply_batch before it touches any other file."""
    _, write_svc, file_ids, _, _ = library
    crash_mid_write(write_svc, file_ids[0], monkeypatch)
    other_plan = write_svc.build_batch_preview([file_ids[1]])[0]
    other_hash = compute_sha256(other_plan.source_path)

    with pytest.raises(PendingRecoveryError, match="interrupted write"):
        write_svc.apply_batch([other_plan])

    assert compute_sha256(other_plan.source_path) == other_hash


def test_an_unexpected_rollback_error_keeps_the_original_failure(
    library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A RuntimeError during rollback is recorded, and the write error still surfaces."""
    repo, write_svc, file_ids, _, _ = library

    def fail_write(_path: Path, _tags: dict[str, str]) -> None:
        raise OSError("write failed")

    def fail_rollback(*_args: Any) -> str:
        raise RuntimeError("rollback failed unexpectedly")

    monkeypatch.setattr(write_svc.tag_adapter, "_write_tags_by_format", fail_write)
    monkeypatch.setattr(write_svc, "_restore_into_place", fail_rollback)
    plan = write_svc.build_batch_preview([file_ids[0]])[0]

    with pytest.raises(OSError, match="write failed"):
        write_svc.apply_single_plan(plan)

    record = repo.list_change_records(file_ids[0])[0]
    assert record.operation_status == OperationStatus.IN_PROGRESS
    assert "rollback failed unexpectedly" in (record.error_message or "")
