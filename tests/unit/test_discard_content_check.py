"""Tests that the restore cleanup keeps a renamed file edited in place before removal."""

from __future__ import annotations

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

WRITTEN_BYTES = b"bytes the write produced"
EDITED_BYTES = b"bytes another program wrote in place"
RENAME_TEMPLATE = "{artist} - {title}"


def discard_leftovers(folder: Path) -> list[str]:
    """Return the names of private discard files left in folder."""
    return [path.name for path in folder.iterdir() if path.name.startswith(DISCARD_NAME_PREFIX)]


def test_discard_keeps_in_place_edit(tmp_path: Path) -> None:
    """Same identity with other content keeps the file under its name."""
    target = tmp_path / "renamed.wav"
    target.write_bytes(WRITTEN_BYTES)
    identity = file_identity(target)
    expected = compute_sha256(target)
    with target.open("r+b") as handle:
        handle.write(EDITED_BYTES)

    assert not discard_if_same_file(target, identity, expected_sha256=expected)

    assert target.read_bytes().startswith(EDITED_BYTES)
    assert discard_leftovers(tmp_path) == []


def test_discard_deletes_matching_file(tmp_path: Path) -> None:
    """Same identity and the expected digest delete the file."""
    target = tmp_path / "renamed.wav"
    target.write_bytes(WRITTEN_BYTES)

    assert discard_if_same_file(
        target, file_identity(target), expected_sha256=compute_sha256(target)
    )

    assert list(tmp_path.iterdir()) == []


def test_restore_keeps_edited_renamed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An in-place edit after the restore began keeps the renamed file and its new bytes."""
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
    renamed = Path(record.new_path)
    real_replace = write_svc._replace_with_staged_copy

    def restore_then_edit(*args: Any) -> str:
        restored_hash = real_replace(*args)
        with renamed.open("r+b") as handle:
            handle.write(EDITED_BYTES)
        return restored_hash

    monkeypatch.setattr(write_svc, "_replace_with_staged_copy", restore_then_edit)

    write_svc.restore_operation(record.operation_id)

    assert renamed.read_bytes().startswith(EDITED_BYTES)
    assert compute_sha256(source) == original_hash
    assert discard_leftovers(source.parent) == []
