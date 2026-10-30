"""Tests that Mark as finished refuses journal paths outside the library root."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_wav_fixture


def test_mark_finished_refuses_a_path_outside_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tampered new_path outside the library root raises, and nothing changes."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MutagenAdapter()
    source = create_wav_fixture(
        tmp_path / "crate" / "track.wav", duration_sec=1.0, tags={"artist": "Old", "title": "T"}
    )
    asset = ImportService(repo, tag_adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")

    def die_mid_write(_path: Path, _tags: dict[str, str]) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(tag_adapter, "_write_tags_by_format", die_mid_write)
    with pytest.raises(KeyboardInterrupt):
        write_svc.apply_single_plan(write_svc.build_batch_preview([asset.id])[0])
    monkeypatch.undo()
    record = write_svc.list_interrupted_operations()[0]
    outside = tmp_path / "elsewhere" / "victim.txt"
    outside.parent.mkdir()
    outside.write_text("keep", encoding="utf-8")
    record.new_path = str(outside)
    repo.upsert_change_record(record)

    with pytest.raises(ValueError, match="escape allowed library root"):
        write_svc.mark_interrupted_finished(record.operation_id)

    stored_asset = repo.get_file_by_id(asset.id)
    assert stored_asset is not None
    assert Path(stored_asset.current_path) == Path(asset.current_path)
    stored = repo.get_change_record(record.operation_id)
    assert stored is not None
    assert stored.operation_status == OperationStatus.IN_PROGRESS
