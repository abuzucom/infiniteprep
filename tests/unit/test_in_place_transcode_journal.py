"""Tests for the in-place transcode journal record."""

from __future__ import annotations

from pathlib import Path

import soundfile as sf

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter, compute_sha256
from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import OperationStatus
from infiniteprep.services.import_service import ImportService
from tests.fixtures.audio_factory import create_wav_fixture


def test_operation_status_has_an_in_progress_value() -> None:
    """The journal status used before an in-place replacement exists and round-trips."""
    assert OperationStatus.IN_PROGRESS.value == "in_progress"
    assert OperationStatus("in_progress") is OperationStatus.IN_PROGRESS


def test_in_place_transcode_replaces_the_file_and_completes_its_journal_record(
    tmp_path: Path,
) -> None:
    """An in-place transcode writes a verified backup, replaces the source, and completes."""
    repo = Repository(DatabaseManager(":memory:"))
    source = create_wav_fixture(
        tmp_path / "crate" / "96k.wav", duration_sec=1.0, sample_rate=96000, subtype="FLOAT"
    )
    original_hash = compute_sha256(source)
    asset = ImportService(repo, MutagenAdapter()).import_paths([source]).imported_files[0]
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")

    result = transcoder.transcode_file(
        asset.id,
        preset=TranscodePreset.XDJ_LOSSLESS_AUTO,
        mode=TranscodeMode.IN_PLACE_BACKED_UP,
    )

    assert result.output_path == source.resolve()
    assert sf.info(str(source)).samplerate == 48000
    assert result.change_record_id is not None
    record = repo.get_change_record(result.change_record_id)
    assert record is not None
    assert record.operation_status == OperationStatus.COMPLETED
    assert record.pre_write_hash == original_hash
    assert record.post_write_hash == compute_sha256(source)
    backup = Path(record.backup_reference)
    assert backup.is_relative_to(tmp_path / "backups")
    assert compute_sha256(backup) == original_hash
