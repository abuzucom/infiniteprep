"""Tests for per-operation backups, verified-copy tag writes, and replaceable-file checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io import mutagen_adapter as adapter_module
from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter, compute_sha256
from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_flac_fixture, create_wav_fixture


@pytest.fixture
def library(tmp_path: Path) -> tuple[Repository, WriteService, str, Path, str]:
    """Import one tagged WAV and approve a new artist on it."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MutagenAdapter()
    source = create_wav_fixture(
        tmp_path / "crate" / "track.wav", duration_sec=1.0, tags={"artist": "Old", "title": "T"}
    )
    original_hash = compute_sha256(source)
    asset = ImportService(repo, tag_adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")
    return repo, write_svc, asset.id, source, original_hash


def test_each_write_keeps_its_own_backup(library: Any) -> None:
    """Two writes to one track leave two backups, and the first still holds the original."""
    repo, write_svc, file_id, source, original_hash = library
    first = write_svc.apply_single_plan(write_svc.build_batch_preview([file_id])[0])
    ReviewService(repo).set_manual_field_value(file_id, FieldName.ARTIST.value, "Digweed")
    second = write_svc.apply_single_plan(write_svc.build_batch_preview([file_id])[0])

    first_backup = Path(first.backup_reference)
    assert first_backup.name == f"{file_id}_{first.operation_id}_track.wav"
    assert first_backup != Path(second.backup_reference)
    assert compute_sha256(first_backup) == original_hash
    assert sorted(p.name for p in source.parent.iterdir()) == ["track.wav"]


def test_a_failed_tag_write_never_touches_the_original(
    library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that corrupts its copy and fails leaves the original bytes and no copy."""
    _, write_svc, file_id, source, original_hash = library

    def corrupt_then_fail(path: Path, _tags: dict[str, str]) -> None:
        assert path != source
        path.write_bytes(b"partial write")
        raise OSError("write failed")

    monkeypatch.setattr(write_svc.tag_adapter, "_write_tags_by_format", corrupt_then_fail)

    with pytest.raises(OSError, match="write failed"):
        write_svc.apply_single_plan(write_svc.build_batch_preview([file_id])[0])

    assert compute_sha256(source) == original_hash
    assert sorted(p.name for p in source.parent.iterdir()) == ["track.wav"]


def test_a_copy_that_fails_verification_is_not_swapped_in(
    library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy whose tags do not read back raises, and the original stays in place."""
    _, write_svc, file_id, source, original_hash = library
    monkeypatch.setattr(write_svc.tag_adapter, "_write_tags_by_format", lambda _p, _t: None)

    with pytest.raises(OSError, match="failed verification"):
        write_svc.apply_single_plan(write_svc.build_batch_preview([file_id])[0])

    assert compute_sha256(source) == original_hash
    assert sorted(p.name for p in source.parent.iterdir()) == ["track.wav"]


def test_a_flac_write_reads_back_through_the_copy(tmp_path: Path) -> None:
    """A FLAC write passes Vorbis read-back verification and lands on the original path."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MutagenAdapter()
    source = create_flac_fixture(tmp_path / "crate" / "track.flac", duration_sec=1.0)
    asset = ImportService(repo, tag_adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.LABEL.value, "Bedrock")
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")

    write_svc.apply_single_plan(write_svc.build_batch_preview([asset.id])[0])

    assert tag_adapter.read_metadata(source).standard_tags[FieldName.LABEL.value] == "Bedrock"
    assert sorted(p.name for p in source.parent.iterdir()) == ["track.flac"]


def test_a_hard_linked_file_is_refused(library: Any) -> None:
    """Preflight refuses a file with a second hard link, because a swap would split them."""
    _, write_svc, file_id, source, _ = library
    os.link(source, source.with_name("linked.wav"))

    plan = write_svc.build_batch_preview([file_id])[0]

    assert not plan.is_safe_to_apply
    assert any("hard links" in error for error in plan.validation_errors)


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX file ownership only")
def test_a_file_owned_by_another_user_is_refused(
    library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight refuses a file whose owner differs from the current user."""
    _, write_svc, file_id, source, _ = library
    other_uid = source.stat().st_uid + 1
    monkeypatch.setattr(adapter_module.os, "getuid", lambda: other_uid)

    plan = write_svc.build_batch_preview([file_id])[0]

    assert not plan.is_safe_to_apply
    assert any("another user" in error for error in plan.validation_errors)


def test_in_place_transcode_backup_names_its_operation(tmp_path: Path) -> None:
    """The in-place transcode backup carries the operation id, so no later backup replaces it."""
    repo = Repository(DatabaseManager(":memory:"))
    source = create_wav_fixture(
        tmp_path / "crate" / "96k.wav", duration_sec=1.0, sample_rate=96000, subtype="FLOAT"
    )
    asset = ImportService(repo, MutagenAdapter()).import_paths([source]).imported_files[0]
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")

    result = transcoder.transcode_file(
        asset.id, preset=TranscodePreset.XDJ_LOSSLESS_AUTO, mode=TranscodeMode.IN_PLACE_BACKED_UP
    )

    assert result.change_record_id is not None
    record = repo.get_change_record(result.change_record_id)
    assert record is not None
    expected = f"{asset.id}_{record.operation_id}_transcode_96k.wav"
    assert Path(record.backup_reference).name == expected
