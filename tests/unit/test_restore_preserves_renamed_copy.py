"""Tests that restoring an interrupted renamed write keeps a copy of the renamed file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.hashing import compute_sha256
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_wav_fixture

UNRELATED_BYTES = b"another program saved this file"
RENAME_TEMPLATE = "{artist} - {title}"


def backup_contents(backup_dir: Path) -> list[bytes]:
    """Return the bytes of every file in the backup directory."""
    return [path.read_bytes() for path in backup_dir.iterdir() if path.is_file()]


@pytest.fixture
def renamed_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[WriteService, str, Path, Path, str]:
    """Run a renaming write that stops right after the rename, the way a process kill would."""
    repo = Repository(DatabaseManager(":memory:"))
    source = create_wav_fixture(
        tmp_path / "crate" / "track_0.wav",
        duration_sec=1.0,
        tags={"artist": "Old", "title": "T0"},
    )
    original_hash = compute_sha256(source)
    file_id = ImportService(repo, MetadataTagAdapter()).import_paths([source]).imported_files[0].id
    ReviewService(repo).set_manual_field_value(file_id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repo, MetadataTagAdapter(), backup_dir=tmp_path / "backups")
    plan = write_svc.build_batch_preview(
        [file_id], rename_template=RENAME_TEMPLATE, enable_rename=True
    )[0]
    real_rename = write_svc.tag_adapter._execute_optional_rename

    def rename_then_die(*args: Any) -> Path:
        real_rename(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(write_svc.tag_adapter, "_execute_optional_rename", rename_then_die)
    with pytest.raises(KeyboardInterrupt):
        write_svc.apply_single_plan(plan)
    monkeypatch.undo()
    operation_id = write_svc.list_interrupted_operations()[0].operation_id
    return write_svc, operation_id, source, plan.destination_path, original_hash


def test_interrupted_restore_keeps_a_copy_of_the_renamed_file(renamed_crash: Any) -> None:
    """The restore removes the renamed file only after a verified copy reaches the backups."""
    write_svc, operation_id, source, renamed, original_hash = renamed_crash
    renamed_bytes = renamed.read_bytes()

    write_svc.restore_interrupted(operation_id)

    assert compute_sha256(source) == original_hash
    assert not renamed.exists()
    assert renamed_bytes in backup_contents(write_svc.backup_dir)


def test_file_that_replaced_the_rename_before_the_restore_survives_in_the_backups(
    renamed_crash: Any,
) -> None:
    """A file put at the renamed path after the crash keeps a verified copy in the backups."""
    write_svc, operation_id, source, renamed, original_hash = renamed_crash
    staged = renamed.with_name("replacement.tmp")
    staged.write_bytes(UNRELATED_BYTES)
    os.replace(staged, renamed)

    write_svc.restore_interrupted(operation_id)

    assert compute_sha256(source) == original_hash
    assert UNRELATED_BYTES in backup_contents(write_svc.backup_dir)
