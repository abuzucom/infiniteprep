"""A shared library folder logs a warning. The write repeats the dropped tag data check."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final

import pytest

from infiniteprep.adapters.metadata_io import metadata_tag_adapter as adapter_module
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import AudioFormat, FieldName, OperationStatus
from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_mp3_fixture
from tests.fixtures.id3v2_bytes import tag_bytes, utf16, v23_frame

SHARED_FOLDER: Final[int] = 0o777
OWNER_ONLY_FOLDER: Final[int] = 0o700
ID3V23_MAJOR: Final[int] = 3
UTF16_ENCODING: Final[bytes] = b"\x01"
# The second v2.3 frame flag byte marks an encrypted frame with 0x40.
V23_ENCRYPTED_FLAGS: Final[bytes] = b"\x00\x40"
ENCRYPTED_FRAME_ID: Final[str] = "GEOB"
WARNING_TEXT: Final[str] = "Other accounts can change files in the library folder"

posix_only = pytest.mark.skipif(os.name == "nt", reason="Windows ignores POSIX mode bits.")


@posix_only
def test_shared_folder_warns_and_opens(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A world-writable library folder opens with a warning that names the fix."""
    folder = tmp_path / "shared"
    folder.mkdir()
    folder.chmod(SHARED_FOLDER)

    with caplog.at_level(logging.WARNING):
        DatabaseManager(folder / "library.db").close()

    assert any(WARNING_TEXT in record.getMessage() for record in caplog.records)
    assert (folder / "library.db").is_file()


@posix_only
def test_private_library_folder_logs_no_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An owner-only library folder opens without the shared folder warning."""
    folder = tmp_path / "private"
    folder.mkdir()
    folder.chmod(OWNER_ONLY_FOLDER)

    with caplog.at_level(logging.WARNING):
        DatabaseManager(folder / "library.db").close()

    assert not any(WARNING_TEXT in record.getMessage() for record in caplog.records)


def preview(tmp_path: Path, source: Path) -> tuple[WriteService, Repository, FileWritePlan]:
    """Import source, approve a new title, and return the service, repository, and plan."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    asset = ImportService(repo, adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.TITLE, "New title")
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")
    return service, repo, service.build_batch_preview([asset.id])[0]


def test_write_with_the_previewed_data_loss_completes(tmp_path: Path) -> None:
    """The same dropped tag data at write time lets the write finish."""
    source = create_mp3_fixture(tmp_path / "encrypted.mp3")
    data = source.read_bytes()
    frames = v23_frame("TIT2", UTF16_ENCODING + utf16("Old")) + v23_frame(
        ENCRYPTED_FRAME_ID, b"\x01secret-bytes", V23_ENCRYPTED_FLAGS
    )
    source.write_bytes(tag_bytes(ID3V23_MAJOR, frames) + data[data.index(b"\xff\xfb") :])
    service, _repo, plan = preview(tmp_path, source)
    assert len(plan.tag_data_warnings) == 1

    record = service.apply_single_plan(plan)

    assert record.operation_status == OperationStatus.COMPLETED
    assert MetadataTagAdapter().read_metadata(source).standard_tags[FieldName.TITLE] == "New title"


def test_write_stops_when_check_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dropped tag data result that changed after the preview stops the write."""
    source = create_mp3_fixture(tmp_path / "clean.mp3", tags={"title": "Old"})
    before = source.read_bytes()
    service, repo, plan = preview(tmp_path, source)
    assert plan.tag_data_warnings == []

    def report_new_loss(_data: bytes) -> tuple[str, ...]:
        return (ENCRYPTED_FRAME_ID,)

    monkeypatch.setitem(adapter_module.DROPPED_TAG_READERS, AudioFormat.MP3, report_new_loss)

    with pytest.raises(ValueError, match="differs from the preview"):
        service.apply_single_plan(plan)

    assert source.read_bytes() == before
    [record] = repo.list_change_records()
    assert record.operation_status == OperationStatus.FAILED
