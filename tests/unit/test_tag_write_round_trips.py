"""Tag writes keep working for partial dates, mixed-case TXXX, and colon file names."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.id3v2 import (
    Id3Tag,
    build_txxx_frame,
    decode_txxx,
    replace_txxx,
    v23_date_text,
)
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    MetadataTagAdapter,
    operation_backup_path,
    portable_backup_name,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_mp3_fixture


def write_year(tmp_path: Path, file_name: str, year: str) -> tuple[Path, OperationStatus]:
    """Import one MP3, approve a YEAR value, write it, and return the path and status."""
    repo = Repository(DatabaseManager(":memory:"))
    adapter = MetadataTagAdapter()
    source = create_mp3_fixture(tmp_path / "crate" / file_name, tags={"artist": "Old"})
    file_id = ImportService(repo, adapter).import_paths([source]).imported_files[0].id
    ReviewService(repo).set_manual_field_value(file_id, FieldName.YEAR.value, year)
    write_svc = WriteService(repo, adapter, backup_dir=tmp_path / "backups")
    plans = write_svc.build_batch_preview([file_id])
    assert all(plan.is_safe_to_apply for plan in plans)
    record = write_svc.apply_batch(plans)[0]
    return Path(record.new_path), record.operation_status


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        ("2020", "2020"),
        ("2020-05", "2020"),
        ("2020-05-17", "2020-05-17"),
        ("2020-05-17T10:20:30Z", "2020-05-17T10:20"),
        ("2020-02-30", "2020"),
        ("Remaster 1994", "Remaster 1994"),
    ],
)
def test_v23_date_text_keeps_the_parts_v23_can_hold(year: str, expected: str) -> None:
    """The comparison value matches what TYER, TDAT, and TIME read back as."""
    assert v23_date_text(year) == expected


@pytest.mark.parametrize("year", ["2020-05", "2020-05-17T10:20:30Z"])
def test_partial_or_precise_year_writes_to_mp3(tmp_path: Path, year: str) -> None:
    """A YEAR that v2.3 cannot hold whole still writes. It reads back as the held part."""
    written_path, status = write_year(tmp_path, "track.mp3", year)

    assert status == OperationStatus.COMPLETED
    stored_year = MetadataTagAdapter().read_metadata(written_path).standard_tags
    assert stored_year.get(FieldName.YEAR.value) == v23_date_text(year)


def test_txxx_replace_ignores_case() -> None:
    """A stale TXXX frame with a different case goes, so readers see one value."""
    tag = Id3Tag(frames=(build_txxx_frame("CatalogNumber", "OLD-1"),))

    updated = replace_txxx(tag, "CATALOGNUMBER", "NEW-2")

    txxx = [decode_txxx(frame) for frame in updated.frames if frame.frame_id == "TXXX"]
    assert txxx == [("CATALOGNUMBER", ["NEW-2"])]


def test_backup_name_replaces_bad_chars(tmp_path: Path) -> None:
    """A colon or backslash becomes an underscore, and the guard accepts the result."""
    name = portable_backup_name("AC:DC \\ Live.mp3")

    assert name == "AC_DC _ Live.mp3"
    assert operation_backup_path(tmp_path, "file", "op", name).name == f"file_op_{name}"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows refuses a colon in a file name.")
def test_file_name_with_a_colon_writes(tmp_path: Path) -> None:
    """macOS stores a Finder slash as a colon. Such a file writes with a backup."""
    written_path, status = write_year(tmp_path, "AC:DC - Track.mp3", "1980")

    assert status == OperationStatus.COMPLETED
    assert written_path.name == "AC:DC - Track.mp3"
    assert any("AC_DC - Track.mp3" in path.name for path in (tmp_path / "backups").iterdir())
