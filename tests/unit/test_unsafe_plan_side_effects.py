"""Tests that an unsafe write plan raises before any backup or file change."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter, compute_sha256
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_wav_fixture


def test_unsafe_plan_leaves_no_backup_and_an_unchanged_source(tmp_path: Path) -> None:
    """A plan that fails preflight creates no backup file and keeps the source bytes."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MutagenAdapter()
    source = create_wav_fixture(
        tmp_path / "crate" / "track.wav", duration_sec=1.0, tags={"artist": "Old", "title": "T"}
    )
    asset = ImportService(repo, tag_adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    backup_dir = tmp_path / "backups"
    write_svc = WriteService(repo, tag_adapter, backup_dir=backup_dir)
    source.write_bytes(source.read_bytes() + b"\x00\x00")
    tampered_hash = compute_sha256(source)
    plan = write_svc.build_batch_preview([asset.id])[0]
    assert not plan.is_safe_to_apply

    with pytest.raises(ValueError, match="preflight"):
        write_svc.apply_single_plan(plan)

    assert not plan.backup_destination_path.exists()
    assert not backup_dir.exists() or list(backup_dir.iterdir()) == []
    assert compute_sha256(source) == tampered_hash
    assert repo.list_change_records(asset.id) == []
