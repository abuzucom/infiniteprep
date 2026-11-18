"""The write preview names tag data that a rewrite cannot carry over."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.write_plan_dialog import format_plan_status
from tests.fixtures.audio_factory import create_flac_fixture, create_mp3_fixture
from tests.fixtures.flac_bytes import vorbis_body
from tests.fixtures.id3v2_bytes import tag_bytes, utf16, v22_frame, v23_frame

ID3V22_MAJOR: Final[int] = 2
ID3V23_MAJOR: Final[int] = 3
# Bit 6 of the v2.2 header flags marks the undefined compression scheme.
V22_COMPRESSION_FLAG: Final[int] = 0x40
# The second v2.3 frame flag byte marks an encrypted frame with 0x40.
V23_ENCRYPTED_FLAGS: Final[bytes] = b"\x00\x40"
UTF16_ENCODING: Final[bytes] = b"\x01"
ENCRYPTED_FRAME_ID: Final[str] = "GEOB"
ENTRY_WITHOUT_SEPARATOR: Final[str] = "DJNOTESWITHOUTVALUE"


def replace_leading_id3_tag(path: Path, tag: bytes) -> Path:
    """Swap the fixture's leading ID3 tag for tag. The MPEG frames stay unchanged."""
    data = path.read_bytes()
    path.write_bytes(tag + data[data.index(b"\xff\xfb") :])
    return path


def preview_title_write(tmp_path: Path, source: Path) -> FileWritePlan:
    """Import source, approve a new title, and return its write preview."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    asset = ImportService(repo, adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.TITLE, "New title")
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")
    return service.build_batch_preview([asset.id])[0]


def test_clean_tag_gets_no_dropped_data_warning(tmp_path: Path) -> None:
    """A tag the codec keeps whole produces no warning."""
    source = create_mp3_fixture(tmp_path / "clean.mp3", tags={"title": "Old"})

    plan = preview_title_write(tmp_path, source)

    assert plan.tag_data_warnings == []
    assert plan.is_safe_to_apply


def test_unreadable_id3_frame_warns_and_still_allows_the_write(tmp_path: Path) -> None:
    """An encrypted frame shows in the preview. The write stays allowed."""
    source = create_mp3_fixture(tmp_path / "encrypted.mp3")
    frames = v23_frame("TIT2", UTF16_ENCODING + utf16("Old")) + v23_frame(
        ENCRYPTED_FRAME_ID, b"\x01secret-bytes", V23_ENCRYPTED_FLAGS
    )
    replace_leading_id3_tag(source, tag_bytes(ID3V23_MAJOR, frames))

    plan = preview_title_write(tmp_path, source)

    assert len(plan.tag_data_warnings) == 1
    assert ENCRYPTED_FRAME_ID in plan.tag_data_warnings[0]
    assert plan.validation_errors == []
    assert plan.is_safe_to_apply


def test_compressed_id3v22_tag_blocks_the_write(tmp_path: Path) -> None:
    """A rewrite would remove a whole compressed ID3v2.2 tag, so the preview blocks it."""
    source = create_mp3_fixture(tmp_path / "compressed.mp3")
    frames = v22_frame("TT2", b"\x00Old title")
    replace_leading_id3_tag(source, tag_bytes(ID3V22_MAJOR, frames, flags=V22_COMPRESSION_FLAG))

    plan = preview_title_write(tmp_path, source)

    assert not plan.is_safe_to_apply
    assert any("ID3v2.2" in error for error in plan.validation_errors)


def test_flac_entry_without_separator_warns(tmp_path: Path) -> None:
    """A Vorbis comment entry without '=' shows in the preview."""
    source = create_flac_fixture(tmp_path / "entry.flac", tags={"title": "Old"})
    data = source.read_bytes()
    old_comment = vorbis_body("InfinitePrep test fixture", "TITLE=Old")
    new_comment = vorbis_body("InfinitePrep test fixture", "TITLE=Old", ENTRY_WITHOUT_SEPARATOR)
    source.write_bytes(_swap_flac_comment(data, old_comment, new_comment))

    plan = preview_title_write(tmp_path, source)

    assert len(plan.tag_data_warnings) == 1
    assert ENTRY_WITHOUT_SEPARATOR in plan.tag_data_warnings[0]
    assert plan.is_safe_to_apply


def test_preview_status_names_the_data_loss(tmp_path: Path) -> None:
    """The Safety Status cell keeps READY and adds the dropped data warning."""
    source = create_mp3_fixture(tmp_path / "status.mp3")
    frames = v23_frame(ENCRYPTED_FRAME_ID, b"\x01secret-bytes", V23_ENCRYPTED_FLAGS)
    replace_leading_id3_tag(source, tag_bytes(ID3V23_MAJOR, frames))

    status = format_plan_status(preview_title_write(tmp_path, source))

    assert status.startswith("READY WITH DATA LOSS: ")
    assert ENCRYPTED_FRAME_ID in status


def _swap_flac_comment(data: bytes, old: bytes, new: bytes) -> bytes:
    """Replace the comment block body old with new and fix the 3-byte block length."""
    start = data.index(old)
    header_length = start - 3
    assert int.from_bytes(data[header_length:start], "big") == len(old)
    return data[:header_length] + len(new).to_bytes(3, "big") + new + data[start + len(old) :]
