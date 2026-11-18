"""Tests that restore cleanup deletes only the renamed file, never a later replacement."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.adapters.metadata_io.no_clobber_move import (
    DISCARD_NAME_PREFIX,
    discard_if_same_file,
    file_identity,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.hashing import compute_sha256
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_wav_fixture

UNRELATED_BYTES = b"another program saved this file"
RENAMED_BYTES = b"the renamed file"
RENAME_TEMPLATE = "{artist} - {title}"


def replace_atomically(target: Path, content: bytes) -> None:
    """Put a new file at target through a rename, so it gets a fresh inode."""
    staged = target.with_name("replacement.tmp")
    staged.write_bytes(content)
    os.replace(staged, target)


def discard_leftovers(folder: Path) -> list[str]:
    """Return the names of private discard files left in folder."""
    return [path.name for path in folder.iterdir() if path.name.startswith(DISCARD_NAME_PREFIX)]


@pytest.fixture
def renamed_write(tmp_path: Path) -> tuple[WriteService, str, Path, Path, str]:
    """Apply a tag write that renames one WAV, and return the service and both paths."""
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
    record = write_svc.apply_single_plan(plan)
    return write_svc, record.operation_id, source, Path(record.new_path), original_hash


def test_restore_removes_the_renamed_file(renamed_write: Any) -> None:
    """A restore puts the source back and removes the renamed copy."""
    write_svc, operation_id, source, renamed, original_hash = renamed_write

    write_svc.restore_operation(operation_id)

    assert compute_sha256(source) == original_hash
    assert not renamed.exists()
    assert discard_leftovers(source.parent) == []


def test_restore_keeps_a_file_that_replaced_the_renamed_one(
    renamed_write: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file another program puts at the renamed path during the restore stays."""
    write_svc, operation_id, source, renamed, original_hash = renamed_write
    real_replace = write_svc._replace_with_staged_copy

    def restore_then_replace(*args: Any) -> str:
        restored_hash = real_replace(*args)
        replace_atomically(renamed, UNRELATED_BYTES)
        return restored_hash

    monkeypatch.setattr(write_svc, "_replace_with_staged_copy", restore_then_replace)

    write_svc.restore_operation(operation_id)

    assert renamed.read_bytes() == UNRELATED_BYTES
    assert compute_sha256(source) == original_hash
    assert discard_leftovers(source.parent) == []


def test_discard_deletes_the_same_file(tmp_path: Path) -> None:
    """The file with the recorded identity is deleted and nothing else remains."""
    target = tmp_path / "renamed.wav"
    target.write_bytes(RENAMED_BYTES)

    assert discard_if_same_file(target, file_identity(target))

    assert list(tmp_path.iterdir()) == []


def test_discard_keeps_a_different_file_at_the_same_name(tmp_path: Path) -> None:
    """A file with another identity returns to its name unchanged."""
    target = tmp_path / "renamed.wav"
    target.write_bytes(RENAMED_BYTES)
    identity = file_identity(target)
    replace_atomically(target, UNRELATED_BYTES)

    assert not discard_if_same_file(target, identity)

    assert target.read_bytes() == UNRELATED_BYTES
    assert [path.name for path in tmp_path.iterdir()] == ["renamed.wav"]


def test_discard_of_a_missing_file_does_nothing(tmp_path: Path) -> None:
    """A path that no longer exists returns False and creates nothing."""
    assert not discard_if_same_file(tmp_path / "gone.wav", (0, 0))

    assert list(tmp_path.iterdir()) == []
