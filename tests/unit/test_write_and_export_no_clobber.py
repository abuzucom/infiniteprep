"""Tests that writes, recovery, transcodes, and playlist exports never replace an unrelated file."""

from __future__ import annotations

import os
import pathlib
import sys
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.adapters.transcoder import safe_transcoder
from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.hashing import compute_sha256
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.playlist_exporter import PlaylistExporter
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_flac_fixture, create_wav_fixture

UNRELATED_BYTES = b"another program saved this file"
RENAME_TEMPLATE = "{artist} - {title}"
RENAMED_NAME = "Sasha - T0.wav"
OLD_PLAYLIST = "#EXTM3U\nold.wav\n"
# Header lines plus one #EXTINF line and one path line.
ONE_TRACK_PLAYLIST_LINES = 4
VERTICAL_TAB = chr(0x0B)
NEXT_LINE = chr(0x85)
LINE_SEPARATOR = chr(0x2028)
TITLE_WITH_SEPARATORS = f"Intro{VERTICAL_TAB}#EXTM3U{NEXT_LINE}Outro{LINE_SEPARATOR}End"


def import_one(repo: Repository, path: Path) -> str:
    """Import one file and return its file ID."""
    return ImportService(repo, MetadataTagAdapter()).import_paths([path]).imported_files[0].id


@pytest.fixture
def tagged_library(tmp_path: Path) -> tuple[Repository, WriteService, str, Path, str]:
    """Import one tagged WAV and approve a new artist for it."""
    repo = Repository(DatabaseManager(":memory:"))
    source = create_wav_fixture(
        tmp_path / "crate" / "track_0.wav",
        duration_sec=1.0,
        tags={"artist": "Old", "title": "T0"},
    )
    file_id = import_one(repo, source)
    ReviewService(repo).set_manual_field_value(file_id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repo, MetadataTagAdapter(), backup_dir=tmp_path / "backups")
    return repo, write_svc, file_id, source, compute_sha256(source)


def build_rename_plan(write_svc: WriteService, file_id: str) -> FileWritePlan:
    """Build a safe plan that renames the track to RENAMED_NAME."""
    plan = write_svc.build_batch_preview(
        [file_id], rename_template=RENAME_TEMPLATE, enable_rename=True
    )[0]
    assert plan.is_safe_to_apply
    assert plan.destination_path.name == RENAMED_NAME
    return plan


def crash_before_rename(write_svc: WriteService, plan: FileWritePlan, monkeypatch: Any) -> str:
    """Stop the write inside the tag step, the way a process kill would."""

    def die(_path: Path, _tags: dict[str, str]) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(write_svc.tag_adapter, "_write_tags_by_format", die)
        with pytest.raises(KeyboardInterrupt):
            write_svc.apply_single_plan(plan)
    return write_svc.list_interrupted_operations()[0].operation_id


def test_rollback_after_a_refused_rename_keeps_the_existing_file(tagged_library: Any) -> None:
    """The rename refuses a file that appeared after the preview, and rollback leaves it."""
    _, write_svc, file_id, source, original_hash = tagged_library
    plan = build_rename_plan(write_svc, file_id)
    plan.destination_path.write_bytes(UNRELATED_BYTES)

    with pytest.raises(FileExistsError):
        write_svc.apply_single_plan(plan)

    assert plan.destination_path.read_bytes() == UNRELATED_BYTES
    assert compute_sha256(source) == original_hash


def test_rename_refuses_a_file_that_the_existence_check_missed(
    tagged_library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that appears between the check and the rename is kept, not replaced.

    An existence check that reports no file stands in for that timing.
    """
    _, write_svc, file_id, source, original_hash = tagged_library
    plan = build_rename_plan(write_svc, file_id)
    destination = plan.destination_path
    destination.write_bytes(UNRELATED_BYTES)
    real_exists = pathlib.Path.exists

    def exists_missing_destination(self: Path, *args: Any, **kwargs: Any) -> bool:
        return False if self == destination else real_exists(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(pathlib.Path, "exists", exists_missing_destination)
        with pytest.raises(FileExistsError):
            write_svc.apply_single_plan(plan)

    assert destination.read_bytes() == UNRELATED_BYTES
    assert compute_sha256(source) == original_hash


def test_restore_after_a_crash_before_the_rename_keeps_the_existing_file(
    tagged_library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery restores the source and leaves the file at the planned name alone."""
    _, write_svc, file_id, source, original_hash = tagged_library
    plan = build_rename_plan(write_svc, file_id)
    plan.destination_path.write_bytes(UNRELATED_BYTES)
    operation_id = crash_before_rename(write_svc, plan, monkeypatch)

    write_svc.restore_interrupted(operation_id)

    assert plan.destination_path.read_bytes() == UNRELATED_BYTES
    assert compute_sha256(source) == original_hash


def test_mark_finished_after_a_crash_before_the_rename_keeps_the_source(
    tagged_library: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record and the asset point at the source, not at the file at the planned name."""
    repo, write_svc, file_id, source, _ = tagged_library
    plan = build_rename_plan(write_svc, file_id)
    plan.destination_path.write_bytes(UNRELATED_BYTES)
    operation_id = crash_before_rename(write_svc, plan, monkeypatch)

    record = write_svc.mark_interrupted_finished(operation_id)

    asset = repo.get_file_by_id(file_id)
    assert asset is not None
    assert Path(record.new_path).resolve() == source.resolve()
    assert Path(asset.current_path).resolve() == source.resolve()
    assert plan.destination_path.read_bytes() == UNRELATED_BYTES


def test_sibling_export_refuses_an_existing_output_file(tmp_path: Path) -> None:
    """A file at the sibling name stays unchanged, and no temporary file is left behind."""
    repo = Repository(DatabaseManager(":memory:"))
    source = create_wav_fixture(tmp_path / "crate" / "track.wav", duration_sec=0.5)
    file_id = import_one(repo, source)
    existing = source.with_name("track_XDJ.wav")
    existing.write_bytes(UNRELATED_BYTES)
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")

    with pytest.raises(FileExistsError):
        transcoder.transcode_file(
            file_id, preset=TranscodePreset.FORCE_WAV_48K_24B, mode=TranscodeMode.SIBLING_EXPORT
        )

    assert existing.read_bytes() == UNRELATED_BYTES
    assert sorted(path.name for path in source.parent.iterdir()) == [
        "track.wav",
        "track_XDJ.wav",
    ]


@pytest.mark.parametrize(
    ("mode", "output_name"),
    [
        (TranscodeMode.SIBLING_EXPORT, "track_XDJ.wav"),
        (TranscodeMode.IN_PLACE_BACKED_UP, "track.wav"),
    ],
)
def test_transcode_refuses_an_output_file_that_appears_during_the_encode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: TranscodeMode, output_name: str
) -> None:
    """The finished output never replaces a file that appeared after the output name check."""
    repo = Repository(DatabaseManager(":memory:"))
    source = create_flac_fixture(tmp_path / "crate" / "track.flac", duration_sec=0.5)
    source_hash = compute_sha256(source)
    file_id = import_one(repo, source)
    output = source.with_name(output_name)
    real_write = safe_transcoder.write_resampled_audio

    def write_then_collide(*args: Any) -> None:
        output.write_bytes(UNRELATED_BYTES)
        real_write(*args)

    monkeypatch.setattr(safe_transcoder, "write_resampled_audio", write_then_collide)
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")

    with pytest.raises(FileExistsError):
        transcoder.transcode_file(file_id, preset=TranscodePreset.FORCE_WAV_48K_24B, mode=mode)

    assert output.read_bytes() == UNRELATED_BYTES
    assert compute_sha256(source) == source_hash
    assert sorted(path.name for path in source.parent.iterdir()) == sorted(
        ["track.flac", output_name]
    )


@pytest.mark.skipif(
    sys.platform != "linux", reason="Only Linux file systems accept a file name that is not UTF-8."
)
def test_failed_playlist_write_keeps_the_previous_playlist(tmp_path: Path) -> None:
    """A track path that UTF-8 cannot encode fails the export and keeps the old playlist."""
    repo = Repository(DatabaseManager(":memory:"))
    source = create_wav_fixture(tmp_path / "crate" / "track.wav", duration_sec=0.5)
    file_id = import_one(repo, source)
    undecodable = Path(os.fsdecode(bytes(tmp_path / "crate") + b"/bad\xff.wav"))
    os.rename(source, undecodable)
    source.symlink_to(undecodable)
    destination = tmp_path / "out" / "crate.m3u8"
    destination.parent.mkdir()
    destination.write_text(OLD_PLAYLIST, encoding="utf-8")

    with pytest.raises(UnicodeEncodeError):
        PlaylistExporter(repo).export_m3u8_report([file_id], destination)

    assert destination.read_text(encoding="utf-8") == OLD_PLAYLIST
    assert [path.name for path in destination.parent.iterdir()] == ["crate.m3u8"]


def test_extinf_text_holds_no_line_separator(tmp_path: Path) -> None:
    """Vertical tab, NEL, and the Unicode line separator do not split the #EXTINF line."""
    repo = Repository(DatabaseManager(":memory:"))
    source = create_wav_fixture(tmp_path / "crate" / "track.wav", duration_sec=0.5)
    file_id = import_one(repo, source)
    ReviewService(repo).set_manual_field_value(
        file_id, FieldName.TITLE.value, TITLE_WITH_SEPARATORS
    )
    destination = tmp_path / "out" / "crate.m3u8"

    PlaylistExporter(repo).export_m3u8_report([file_id], destination)

    lines = destination.read_text(encoding="utf-8").splitlines()
    assert len(lines) == ONE_TRACK_PLAYLIST_LINES
    assert lines[2].endswith("Intro #EXTM3U Outro End")
