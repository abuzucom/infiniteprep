"""Import, analysis, transcode, job state, and database fixes return correct results."""

from __future__ import annotations

import logging
import os
import sqlite3
import stat
import sys
from pathlib import Path

import numpy as np
import pytest

from infiniteprep.adapters.analyzers.local_dsp import estimate_key_from_chroma
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
from infiniteprep.domain.models import AnalysisResult
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import JobOrchestrator
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import create_wav_fixture

SILENT_SAMPLES = 8192
SAMPLE_RATE = 44100
# A tag write limit below any real output size, so the size check runs on a short file.
TINY_TAG_WRITE_LIMIT = 1000
# A 0.5 second 44.1 kHz stereo fixture resamples to 24000 frames at 48 kHz. As 24-bit stereo
# those frames take 24000 * 2 * 3 bytes before the WAV header.
EXACT_SAMPLE_BYTES = 24000 * 2 * 3
CORRUPT_DATABASE_BYTES = b"this file is not an SQLite database" * 64


class FailingAnalyzer:
    """An analyzer that raises for every file, as a damaged decoder would."""

    analyzer_name = "failing"
    analyzer_version = "1"

    def analyze(self, audio_path: Path, file_id: str) -> AnalysisResult:
        """Raise the error a damaged file produces."""
        raise ValueError(f"cannot decode {audio_path.name}")


def make_repo() -> Repository:
    """Return a repository on a private in-memory database."""
    return Repository(DatabaseManager(":memory:"))


def import_one(repo: Repository, path: Path) -> str:
    """Import one file and return its asset id."""
    return ImportService(repo, MetadataTagAdapter()).import_paths([path]).imported_files[0].id


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="POSIX permissions only block a non-root user.",
)
def test_import_skips_unreadable_subfolder(tmp_path: Path) -> None:
    """A protected folder on a drive root no longer aborts the whole import."""
    root = tmp_path / "drive"
    create_wav_fixture(root / "music" / "track.wav", duration_sec=0.5)
    locked = root / "System Volume Information"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        report = ImportService(make_repo(), MetadataTagAdapter()).import_paths([root])
    finally:
        locked.chmod(stat.S_IRWXU)

    assert [Path(asset.current_path).name for asset in report.imported_files] == ["track.wav"]


def test_overlapping_selection_imports_once(tmp_path: Path) -> None:
    """A file named directly and through its folder imports once and counts once."""
    track = create_wav_fixture(tmp_path / "crate" / "track.wav", duration_sec=0.5)

    report = ImportService(make_repo(), MetadataTagAdapter()).import_paths(
        [tmp_path / "crate", track]
    )

    assert report.scanned_count == 1
    assert len(report.imported_files) == 1
    assert report.previously_imported_paths == []


def test_silent_audio_has_no_key() -> None:
    """Silence carries no pitch, so the analyzer proposes no key."""
    assert estimate_key_from_chroma(np.zeros(SILENT_SAMPLES), SAMPLE_RATE) == (None, None, 0.0)


def job_rows(repo: Repository) -> dict[str, tuple[str, str | None]]:
    """Return the status and error text of each job row, keyed by job type."""
    rows = repo.db.connect().execute("SELECT job_type, status, last_error FROM jobs").fetchall()
    return {row["job_type"]: (row["status"], row["last_error"]) for row in rows}


def test_failed_job_records_its_error(tmp_path: Path) -> None:
    """The failing step's job row reads failed and names the error. None stays queued."""
    repo = make_repo()
    file_id = import_one(repo, create_wav_fixture(tmp_path / "track.wav", duration_sec=0.5))

    JobOrchestrator(repository=repo, audio_analyzer=FailingAnalyzer()).process_files([file_id])

    rows = job_rows(repo)
    status, error = rows["audio_analysis"]
    assert status == "failed"
    assert error is not None
    assert "ValueError" in error
    assert rows["fingerprint"][0] == "completed"
    assert repo.list_pending_jobs() == []


def test_raise_on_failure_records_first(tmp_path: Path) -> None:
    """The renamed keyword raises for an expected job error once the failure is stored."""
    repo = make_repo()
    file_id = import_one(repo, create_wav_fixture(tmp_path / "track.wav", duration_sec=0.5))
    orchestrator = JobOrchestrator(repository=repo, audio_analyzer=FailingAnalyzer())

    with pytest.raises(RuntimeError, match="Processing failed"):
        orchestrator.process_files([file_id], raise_on_failure=True)

    assert job_rows(repo)["audio_analysis"][0] == "failed"


def test_transcode_refuses_untaggable_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A WAV output past the tag write limit fails up front. Nothing is written."""
    monkeypatch.setattr(safe_transcoder, "MAX_WAV_REWRITE_BYTES", TINY_TAG_WRITE_LIMIT)
    repo = make_repo()
    source = create_wav_fixture(
        tmp_path / "crate" / "tagged.wav", duration_sec=0.5, tags={"artist": "Deep Dish"}
    )
    file_id = import_one(repo, source)
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")

    with pytest.raises(ValueError, match="tag write limit"):
        transcoder.transcode_file(
            file_id,
            preset=TranscodePreset.FORCE_WAV_48K_24B,
            mode=TranscodeMode.SIBLING_EXPORT,
        )

    assert sorted(path.name for path in source.parent.iterdir()) == ["tagged.wav"]


def test_transcode_limit_counts_container_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Samples that fit the limit exactly still fail, since the header adds bytes."""
    monkeypatch.setattr(safe_transcoder, "MAX_WAV_REWRITE_BYTES", EXACT_SAMPLE_BYTES)
    repo = make_repo()
    file_id = import_one(
        repo,
        create_wav_fixture(
            tmp_path / "crate" / "tagged.wav", duration_sec=0.5, tags={"artist": "Deep Dish"}
        ),
    )
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")

    with pytest.raises(ValueError, match="tag write limit"):
        transcoder.transcode_file(
            file_id,
            preset=TranscodePreset.FORCE_WAV_48K_24B,
            mode=TranscodeMode.SIBLING_EXPORT,
        )


def test_untagged_file_ignores_tag_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file with no tags to copy never reaches the tag writer, so the limit does not apply."""
    monkeypatch.setattr(safe_transcoder, "MAX_WAV_REWRITE_BYTES", TINY_TAG_WRITE_LIMIT)
    repo = make_repo()
    file_id = import_one(
        repo, create_wav_fixture(tmp_path / "crate" / "plain.wav", duration_sec=0.5)
    )
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")

    result = transcoder.transcode_file(
        file_id, preset=TranscodePreset.FORCE_WAV_48K_24B, mode=TranscodeMode.SIBLING_EXPORT
    )

    assert result.output_path.exists()


def test_non_database_file_releases_handle(tmp_path: Path) -> None:
    """The failed open closes the file. A restore can then rename it, also on Windows."""
    library = tmp_path / "library.db"
    library.write_bytes(CORRUPT_DATABASE_BYTES)

    with pytest.raises(sqlite3.DatabaseError):
        DatabaseManager(library)

    moved = tmp_path / "library.db.bad"
    os.replace(library, moved)
    assert moved.exists()


def test_failed_queued_write_logs_traceback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The warning for a failed queued write carries the traceback for diagnosis."""
    repo = make_repo()
    adapter = MetadataTagAdapter()
    source = create_wav_fixture(tmp_path / "crate" / "track.wav", duration_sec=0.5)
    file_id = import_one(repo, source)
    ReviewService(repo).set_manual_field_value(file_id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repo, adapter, backup_dir=tmp_path / "backups")
    plans = write_svc.build_batch_preview([file_id])
    source.unlink()

    with caplog.at_level(logging.WARNING, logger="infiniteprep.services.write_service"):
        result = write_svc.apply_batch_continuing(plans)

    assert len(result.failures) == 1
    warnings = [record for record in caplog.records if "Queued write failed" in record.message]
    assert warnings
    assert warnings[0].exc_info is not None
