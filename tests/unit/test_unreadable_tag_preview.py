"""The write preview blocks a file whose tag the dropped data check cannot read."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io import metadata_tag_adapter as adapter_module
from infiniteprep.adapters.metadata_io.id3v2 import Id3FormatError
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import AudioFormat, FieldName
from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_mp3_fixture


def preview_title_write(tmp_path: Path, source: Path) -> FileWritePlan:
    """Import source, approve a new title, and return its write preview."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    asset = ImportService(repo, adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.TITLE, "New title")
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")
    return service.build_batch_preview([asset.id])[0]


def test_unparsable_tag_blocks_the_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tag the check cannot parse blocks the plan and names the reason."""
    source = create_mp3_fixture(tmp_path / "damaged.mp3", tags={"title": "Old"})

    def fail_parse(_data: bytes) -> tuple[str, ...]:
        raise Id3FormatError("ID3v2 size runs past the end of the data")

    monkeypatch.setitem(adapter_module.DROPPED_TAG_READERS, AudioFormat.MP3, fail_parse)

    plan = preview_title_write(tmp_path, source)

    assert not plan.is_safe_to_apply
    assert any(
        "tag cannot be read" in error and "runs past the end" in error
        for error in plan.validation_errors
    )
    assert plan.tag_data_warnings == []


def test_unreadable_file_blocks_the_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read failure in the check blocks the plan instead of reporting READY."""
    source = create_mp3_fixture(tmp_path / "locked.mp3", tags={"title": "Old"})

    def fail_read(_data: bytes) -> tuple[str, ...]:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setitem(adapter_module.DROPPED_TAG_READERS, AudioFormat.MP3, fail_read)

    plan = preview_title_write(tmp_path, source)

    assert not plan.is_safe_to_apply
    assert any("cannot be read for the tag check" in error for error in plan.validation_errors)
