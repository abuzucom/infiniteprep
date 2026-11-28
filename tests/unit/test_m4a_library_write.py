"""Behavioral tests for M4A tag writes through the library services."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.audio_payload import compute_audio_payload_digest
from infiniteprep.adapters.metadata_io.audio_properties import find_m4a_payload
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import AudioFormat, FieldName, OperationStatus
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures import synthetic_audio as sa
from tests.fixtures.mp4_bytes import m4a_with_sound_track


def create_m4a_fixture(file_path: Path, *, protected: bool = False) -> Path:
    """Write an M4A fixture with the media offsets a tag write has to move."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if protected:
        file_path.write_bytes(m4a_with_sound_track(entry_type=b"drms"))
    else:
        file_path.write_bytes(sa.build_m4a_aac_bytes(chunk_offsets=True))
    return file_path


def import_track(repo: Repository, adapter: MetadataTagAdapter, path: Path):
    """Import the fixture and approve a title for the write."""
    asset = ImportService(repo, adapter).import_paths([path]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.TITLE, "New title")
    return asset


def test_library_write_stores_the_tags(tmp_path: Path) -> None:
    """An approved title reaches an M4A file and reads back."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    source = create_m4a_fixture(tmp_path / "track.m4a")
    before = source.read_bytes()
    asset = import_track(repo, adapter, source)
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")

    record = service.apply_single_plan(service.build_batch_preview([asset.id])[0])

    assert source.read_bytes() != before
    assert adapter.read_metadata(source).standard_tags[FieldName.TITLE] == "New title"
    assert record.operation_status == OperationStatus.COMPLETED


def test_library_write_keeps_media_bytes(tmp_path: Path) -> None:
    """The media data keeps its bytes through a library write."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    source = create_m4a_fixture(tmp_path / "track.m4a")
    before = source.read_bytes()
    start, end = find_m4a_payload(before)[0]
    asset = import_track(repo, adapter, source)
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")

    service.apply_single_plan(service.build_batch_preview([asset.id])[0])

    after = source.read_bytes()
    new_start, new_end = find_m4a_payload(after)[0]
    assert after[new_start:new_end] == before[start:end]


def test_audio_digest_survives_the_write(tmp_path: Path) -> None:
    """The audio digest stays equal across a write that moves the media data."""
    source = create_m4a_fixture(tmp_path / "track.m4a")
    before = compute_audio_payload_digest(source, AudioFormat.M4A)
    adapter = MetadataTagAdapter()

    adapter._write_tags_by_format(source, {"artist": "Artist", "title": "Title"})

    assert compute_audio_payload_digest(source, AudioFormat.M4A) == before


def test_protected_file_fails_the_preview(tmp_path: Path) -> None:
    """A protected M4A file fails the preview, so no file is touched."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    source = create_m4a_fixture(tmp_path / "track.m4a", protected=True)
    before = source.read_bytes()
    asset = import_track(repo, adapter, source)
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")

    plan = service.build_batch_preview([asset.id])[0]

    assert any("DRM" in error for error in plan.validation_errors)
    assert source.read_bytes() == before


def test_payload_change_fails_the_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A changed media byte fails the write and keeps the original file."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    source = create_m4a_fixture(tmp_path / "track.m4a")
    before = source.read_bytes()
    asset = import_track(repo, adapter, source)
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")
    plan = service.build_batch_preview([asset.id])[0]
    write_tags = adapter._write_m4a_tags

    def write_then_corrupt(path: Path, tags: dict[str, str]) -> None:
        write_tags(path, tags)
        data = bytearray(path.read_bytes())
        start, end = find_m4a_payload(bytes(data))[0]
        data[start + (end - start) // 2] ^= 1
        path.write_bytes(bytes(data))

    monkeypatch.setattr(adapter, "_write_m4a_tags", write_then_corrupt)

    with pytest.raises(OSError, match="audio payload changed"):
        service.apply_single_plan(plan)

    assert source.read_bytes() == before
    [record] = repo.list_change_records()
    assert record.operation_status == OperationStatus.FAILED
