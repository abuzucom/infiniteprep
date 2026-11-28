"""Behavioral tests for writing tags to STEM.MP4 files.

A stem file holds a mixdown track, one track per stem, and a stem atom that names the stems.
The writer keeps that atom byte for byte and moves the chunk offsets of every track.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.audio_payload import compute_audio_payload_digest
from infiniteprep.adapters.metadata_io.audio_properties import find_m4a_payload
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.adapters.metadata_io.mp4_writer import write_m4a_tags
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import AudioFormat, FieldName, OperationStatus
from infiniteprep.domain.models import FileAsset
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures import synthetic_audio as sa

TAGS = {"artist": "Artist", "title": "Title"}
# The fixture writes AAC frames of this size into the media data.
FRAME_BYTES = 743
TRACK_COUNT = 5


def _stem_box_bytes(data: bytes) -> bytes:
    """Return the raw bytes of the stem atom."""
    position = data.find(b"stem")
    size = struct.unpack(">I", data[position - 4 : position])[0]
    return data[position - 4 : position - 4 + size]


def _track_offsets(data: bytes) -> list[list[int]]:
    """Return the chunk offsets of every track."""
    found: list[list[int]] = []
    position = 0
    while True:
        position = data.find(b"stco", position)
        if position < 0:
            return found
        body = data[position + 4 :]
        count = struct.unpack(">I", body[4:8])[0]
        found.append(
            [struct.unpack(">I", body[8 + index * 4 : 12 + index * 4])[0] for index in range(count)]
        )
        position += 4


def _track_samples(data: bytes) -> list[bytes]:
    """Return the samples that every track holds, read through its own offset table."""
    return [
        b"".join(data[start : start + FRAME_BYTES] for start in offsets)
        for offsets in _track_offsets(data)
    ]


def _import_track(repo: Repository, adapter: MetadataTagAdapter, path: Path) -> FileAsset:
    """Import the fixture and approve a title for the write."""
    asset = ImportService(repo, adapter).import_paths([path]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.TITLE, "New title")
    return asset


def test_stem_tracks_keep_their_samples() -> None:
    """A tag write leaves the samples of every track and the stem atom unchanged."""
    data = sa.build_stem_mp4_bytes(chunk_offsets=True)
    written = write_m4a_tags(data, TAGS)
    assert _track_samples(written) == _track_samples(data)
    assert _stem_box_bytes(written) == _stem_box_bytes(data)


def test_stem_offsets_move_with_media() -> None:
    """The chunk offset table of every track moves with the media data."""
    data = sa.build_stem_mp4_bytes(chunk_offsets=True)
    written = write_m4a_tags(data, TAGS)
    shift = len(written) - len(data)
    assert shift > 0
    before = _track_offsets(data)
    after = _track_offsets(written)
    assert len(before) == len(after) == TRACK_COUNT
    assert after == [[offset + shift for offset in offsets] for offsets in before]


def test_stem_digest_stays_equal(tmp_path: Path) -> None:
    """The audio digest of a stem file stays equal across a write."""
    data = sa.build_stem_mp4_bytes(chunk_offsets=True)
    source = tmp_path / "track.mp4"
    source.write_bytes(data)
    result = tmp_path / "written.mp4"
    result.write_bytes(write_m4a_tags(data, TAGS))
    assert compute_audio_payload_digest(result, AudioFormat.M4A) == compute_audio_payload_digest(
        source, AudioFormat.M4A
    )


def test_stem_box_joins_payload_ranges() -> None:
    """The locator covers the stem atom after the media range."""
    data = sa.build_stem_mp4_bytes(chunk_offsets=True)
    ranges = find_m4a_payload(data)
    assert len(ranges) == 2
    assert data[ranges[1][0] : ranges[1][1]] == _stem_box_bytes(data)[8:]


def test_changed_stem_box_fails_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stem atom that changes during a write fails the write and keeps the file."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    source = tmp_path / "track.mp4"
    source.write_bytes(sa.build_stem_mp4_bytes(chunk_offsets=True))
    before = source.read_bytes()
    asset = _import_track(repo, adapter, source)
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")
    plan = service.build_batch_preview([asset.id])[0]
    write_tags = adapter._write_m4a_tags

    def write_then_corrupt(path: Path, tags: dict[str, str]) -> None:
        write_tags(path, tags)
        data = bytearray(path.read_bytes())
        position = data.find(b"stem")
        data[position + 8] ^= 1
        path.write_bytes(bytes(data))

    monkeypatch.setattr(adapter, "_write_m4a_tags", write_then_corrupt)

    with pytest.raises(OSError, match="audio payload changed"):
        service.apply_single_plan(plan)

    assert source.read_bytes() == before
    [record] = repo.list_change_records()
    assert record.operation_status == OperationStatus.FAILED


def test_changed_track_byte_fails_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A track sample that changes during a write fails the write and keeps the file."""
    adapter = MetadataTagAdapter()
    repo = Repository(DatabaseManager(":memory:"))
    source = tmp_path / "track.mp4"
    source.write_bytes(sa.build_stem_mp4_bytes(chunk_offsets=True))
    before = source.read_bytes()
    asset = _import_track(repo, adapter, source)
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")
    plan = service.build_batch_preview([asset.id])[0]
    write_tags = adapter._write_m4a_tags

    def write_then_corrupt(path: Path, tags: dict[str, str]) -> None:
        write_tags(path, tags)
        data = bytearray(path.read_bytes())
        start = find_m4a_payload(bytes(data))[0][0]
        data[start + 16] ^= 1
        path.write_bytes(bytes(data))

    monkeypatch.setattr(adapter, "_write_m4a_tags", write_then_corrupt)

    with pytest.raises(OSError, match="audio payload changed"):
        service.apply_single_plan(plan)

    assert source.read_bytes() == before
    [record] = repo.list_change_records()
    assert record.operation_status == OperationStatus.FAILED
