"""Exercise payload verification through library writes and transcode tagging."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.domain.models import FileAsset
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import (
    create_aiff_fixture,
    create_flac_fixture,
    create_mp3_fixture,
    create_wav_fixture,
)

FORM_HEADER_BYTES = 12
CHUNK_HEADER_BYTES = 8
SSND_FIELDS_BYTES = 8
FORMATS = (
    ("mp3", create_mp3_fixture),
    ("flac", create_flac_fixture),
    ("wav", create_wav_fixture),
    ("aiff", create_aiff_fixture),
)


def payload_span(data: bytes, suffix: str) -> tuple[int, int]:
    """Find the audio in the generated fixtures using their known layout."""
    if suffix == "mp3":
        return data.index(b"\xff\xfb"), len(data)
    if suffix == "flac":
        return data.index(b"\xff\xf8"), len(data)
    if suffix == "wav":
        header = data.index(b"data", FORM_HEADER_BYTES)
        start = header + CHUNK_HEADER_BYTES
        return start, start + int.from_bytes(data[header + 4 : start], "little")
    header = data.index(b"SSND", FORM_HEADER_BYTES)
    body = header + CHUNK_HEADER_BYTES
    return body + SSND_FIELDS_BYTES, body + int.from_bytes(data[header + 4 : body], "big")


def corrupt_audio_after_tagging(
    adapter: MetadataTagAdapter,
    suffix: str,
    monkeypatch: pytest.MonkeyPatch,
    concurrent_edit: Callable[[], None] | None = None,
) -> None:
    """Let the real format writer run, then flip one payload byte on its working file."""
    writer_name = f"_write_{suffix}_tags"
    write_tags = getattr(adapter, writer_name)

    def write_then_corrupt(path: Path, tags: dict[str, str]) -> None:
        write_tags(path, tags)
        data = bytearray(path.read_bytes())
        start, end = payload_span(data, suffix)
        data[start + (end - start) // 2] ^= 1
        path.write_bytes(data)
        if concurrent_edit is not None:
            concurrent_edit()
        assert adapter.read_metadata(path).standard_tags[FieldName.TITLE] == "New title"

    monkeypatch.setattr(adapter, writer_name, write_then_corrupt)


def import_track(repo: Repository, adapter: MetadataTagAdapter, path: Path) -> FileAsset:
    """Import the fixture and approve a title for the write."""
    asset = ImportService(repo, adapter).import_paths([path]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.TITLE, "New title")
    return asset


@pytest.mark.parametrize(("suffix", "factory"), FORMATS)
def test_library_tag_write_preserves_audio_payload(
    tmp_path: Path, suffix: str, factory: Callable[..., Path]
) -> None:
    """Write and read back a tag while retaining the original audio bytes."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    source = factory(tmp_path / f"track.{suffix}")
    before = source.read_bytes()
    start, end = payload_span(before, suffix)
    asset = import_track(repo, adapter, source)
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")

    record = service.apply_single_plan(service.build_batch_preview([asset.id])[0])

    after = source.read_bytes()
    new_start, new_end = payload_span(after, suffix)
    assert after[new_start:new_end] == before[start:end]
    assert after != before
    assert adapter.read_metadata(source).standard_tags[FieldName.TITLE] == "New title"
    assert record.operation_status == OperationStatus.COMPLETED


@pytest.mark.parametrize(("suffix", "factory"), FORMATS)
def test_payload_mismatch_keeps_source_and_blocks_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str, factory: Callable[..., Path]
) -> None:
    """A parseable staged copy with modified audio never replaces or renames the source."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    source = factory(tmp_path / f"track.{suffix}")
    before = source.read_bytes()
    asset = import_track(repo, adapter, source)
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")
    plan = service.build_batch_preview([asset.id], "{title}", enable_rename=True)[0]
    assert plan.destination_path != source
    corrupt_audio_after_tagging(adapter, suffix, monkeypatch)

    with pytest.raises(OSError, match="audio payload changed"):
        service.apply_single_plan(plan)

    assert source.read_bytes() == before
    assert not plan.destination_path.exists()
    assert not list(tmp_path.glob(".*.staged*"))
    [record] = repo.list_change_records()
    assert record.operation_status == OperationStatus.FAILED
    assert record.error_message and "audio payload changed" in record.error_message
    assert Path(record.backup_reference).read_bytes() == before
    after = repo.get_file_by_id(asset.id)
    assert after is not None
    assert after.current_path == asset.current_path
    assert after.content_hash == asset.content_hash


def test_payload_mismatch_preserves_a_concurrent_source_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed staged write leaves another program's edit in place."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    source = create_wav_fixture(tmp_path / "track.wav")
    asset = import_track(repo, adapter, source)
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")
    plan = service.build_batch_preview([asset.id])[0]
    external_bytes = source.read_bytes() + b"external edit"
    corrupt_audio_after_tagging(
        adapter, "wav", monkeypatch, lambda: source.write_bytes(external_bytes)
    )

    with pytest.raises(OSError, match="audio payload changed"):
        service.apply_single_plan(plan)

    assert source.read_bytes() == external_bytes
    [record] = repo.list_change_records()
    assert record.operation_status == OperationStatus.FAILED


def test_transcode_payload_mismatch_prevents_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed tag write discards the new transcode and keeps the original."""
    repo = Repository(DatabaseManager(":memory:"))
    source = create_wav_fixture(tmp_path / "track.wav", sample_rate=96000)
    before = source.read_bytes()
    asset = import_track(repo, MetadataTagAdapter(), source)
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")
    corrupt_audio_after_tagging(transcoder.tag_adapter, "wav", monkeypatch)

    with pytest.raises(OSError, match="audio payload changed"):
        transcoder.transcode_file(
            asset.id,
            preset=TranscodePreset.FORCE_WAV_44K_16B,
            mode=TranscodeMode.IN_PLACE_BACKED_UP,
        )

    assert source.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["backups", "track.wav"]
    [record] = repo.list_change_records()
    assert record.operation_status == OperationStatus.FAILED


def test_write_rejects_a_wav_without_locatable_audio(tmp_path: Path) -> None:
    """A valid format header alone does not let the writer verify unknown audio."""
    adapter = MetadataTagAdapter()
    source = create_wav_fixture(tmp_path / "track.wav")
    data = source.read_bytes()
    start, _ = payload_span(data, "wav")
    header = start - CHUNK_HEADER_BYTES
    unknown = data[:header] + b"JUNK" + data[header + 4 :]
    source.write_bytes(unknown)

    with pytest.raises(OSError, match="identify the audio payload"):
        adapter._write_tags_by_format(source, {FieldName.TITLE: "New title"})

    assert source.read_bytes() == unknown
