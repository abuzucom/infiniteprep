"""Tests that Restore from backup refuses journal paths outside the library root."""

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


@pytest.mark.parametrize("tampered_field", ["previous_path", "new_path"])
def test_restore_interrupted_refuses_a_path_outside_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tampered_field: str
) -> None:
    """A tampered journal path outside the library root raises, and no file changes."""
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
    victim = tmp_path / "elsewhere" / "victim.wav"
    victim.parent.mkdir()
    victim.write_bytes(b"keep")
    # upsert_change_record never rewrites previous_path, so tamper the stored row the way an
    # edited database file would. The column name comes from the fixed parametrize list.
    conn = repo.db.connect()
    conn.execute(
        f"UPDATE change_records SET {tampered_field} = ? WHERE operation_id = ?",
        (str(victim), record.operation_id),
    )
    conn.commit()

    with pytest.raises(ValueError, match="escape allowed library root"):
        write_svc.restore_interrupted(record.operation_id)

    assert victim.read_bytes() == b"keep"
    stored = repo.get_change_record(record.operation_id)
    assert stored is not None
    assert stored.operation_status == OperationStatus.IN_PROGRESS
