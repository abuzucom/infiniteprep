"""Tests for swap failures, symlinks, apply-time rechecks, and safe backup names."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io import mutagen_adapter as adapter_module
from infiniteprep.adapters.metadata_io.mutagen_adapter import (
    MutagenAdapter,
    compute_sha256,
    copy_to_new_verified_file,
    operation_backup_path,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_wav_fixture

MISMATCHED_SHA256 = "0" * 64


@pytest.fixture
def library(tmp_path: Path) -> tuple[WriteService, str, Path, str]:
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
    return write_svc, asset.id, source, original_hash


def test_a_failed_swap_keeps_the_original_and_removes_the_copy(
    library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A swap that fails after verification leaves the original bytes and no staged copy."""
    write_svc, file_id, source, original_hash = library
    real_replace = os.replace

    def refuse_swap(src: Any, dst: Any) -> None:
        if ".staged" in Path(src).name:
            raise OSError("device busy")
        real_replace(src, dst)

    monkeypatch.setattr(adapter_module.os, "replace", refuse_swap)

    with pytest.raises(OSError, match="device busy"):
        write_svc.apply_single_plan(write_svc.build_batch_preview([file_id])[0])

    assert compute_sha256(source) == original_hash
    assert sorted(p.name for p in source.parent.iterdir()) == ["track.wav"]


def test_a_symlinked_file_is_refused(library: Any, tmp_path: Path) -> None:
    """Preflight refuses a symlink, because the swap would replace the link, not its target."""
    write_svc, file_id, source, _ = library
    target = tmp_path / "elsewhere" / "track.wav"
    target.parent.mkdir()
    source.rename(target)
    try:
        source.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink creation needs a privilege this runner lacks ({exc}).")

    plan = write_svc.build_batch_preview([file_id])[0]

    assert not plan.is_safe_to_apply
    assert any("symbolic link" in error for error in plan.validation_errors)


def test_apply_rechecks_hard_links_added_after_preview(library: Any) -> None:
    """A hard link made after preview stops the write before any file change."""
    write_svc, file_id, source, original_hash = library
    plan = write_svc.build_batch_preview([file_id])[0]
    assert plan.is_safe_to_apply
    os.link(source, source.with_name("linked.wav"))

    with pytest.raises(ValueError, match="hard links"):
        write_svc.apply_single_plan(plan)

    assert compute_sha256(source) == original_hash


@pytest.mark.parametrize("bad_part", ["..", "", "nested/name.wav"])
def test_backup_names_refuse_directory_parts(tmp_path: Path, bad_part: str) -> None:
    """A name part that could leave the backup folder raises instead of building a path."""
    with pytest.raises(ValueError, match="not a plain file name"):
        operation_backup_path(tmp_path, "file", "operation", bad_part)


def test_a_new_backup_never_replaces_an_existing_file(tmp_path: Path) -> None:
    """Exclusive creation refuses an existing destination and leaves its bytes alone."""
    source = tmp_path / "source.bin"
    source.write_bytes(b"new")
    existing = tmp_path / "backups" / "taken.bin"
    existing.parent.mkdir()
    existing.write_bytes(b"older backup")

    with pytest.raises(FileExistsError, match="refusing to replace"):
        copy_to_new_verified_file(source, existing, compute_sha256(source))

    assert existing.read_bytes() == b"older backup"


def test_a_backup_that_fails_verification_is_removed(tmp_path: Path) -> None:
    """A copy whose hash does not match raises, and no unverified backup stays behind."""
    source = tmp_path / "source.bin"
    source.write_bytes(b"content")
    destination = tmp_path / "backups" / "copy.bin"

    with pytest.raises(OSError, match="verification failed"):
        copy_to_new_verified_file(source, destination, MISMATCHED_SHA256)

    assert not destination.exists()
