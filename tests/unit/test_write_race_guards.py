"""Tests that a write keeps outside edits made during it and refuses unportable backup names."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.mutagen_adapter import (
    MutagenAdapter,
    operation_backup_path,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_wav_fixture

OUTSIDE_EDIT = b"saved by another program during the write"


def test_an_outside_edit_during_the_write_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source change before the swap stops the write, and no rollback replaces the edit."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MutagenAdapter()
    source = create_wav_fixture(
        tmp_path / "crate" / "track.wav", duration_sec=1.0, tags={"artist": "Old", "title": "T"}
    )
    asset = ImportService(repo, tag_adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")
    real_write = tag_adapter._write_tags_by_format

    def write_while_another_program_saves(path: Path, tags: dict[str, str]) -> None:
        real_write(path, tags)
        source.write_bytes(OUTSIDE_EDIT)

    monkeypatch.setattr(tag_adapter, "_write_tags_by_format", write_while_another_program_saves)

    with pytest.raises(OSError, match="changed in another program"):
        write_svc.apply_single_plan(write_svc.build_batch_preview([asset.id])[0])

    assert source.read_bytes() == OUTSIDE_EDIT
    assert sorted(p.name for p in source.parent.iterdir()) == ["track.wav"]
    record = repo.list_change_records(asset.id)[0]
    assert record.operation_status == OperationStatus.FAILED


@pytest.mark.parametrize("bad_part", ["a\\b.wav", "c:stream.wav", "   ", "line\nbreak.wav"])
def test_backup_names_refuse_unportable_parts(tmp_path: Path, bad_part: str) -> None:
    """Backslash, colon, blank, and control characters raise on every platform."""
    with pytest.raises(ValueError, match="not a plain file name"):
        operation_backup_path(tmp_path, "file", "operation", bad_part)


def test_a_plain_backup_name_keeps_every_part(tmp_path: Path) -> None:
    """A valid name joins its parts in order inside the backup folder."""
    path = operation_backup_path(tmp_path, "file", "op", "My Track (Mix).wav", kind="transcode")

    assert path == tmp_path / "file_op_transcode_My Track (Mix).wav"
