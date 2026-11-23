"""Behavioral tests for the providers behind the desktop Identify & Analyze batch."""

from __future__ import annotations

from pathlib import Path

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import TaskStatus
from infiniteprep.main import build_desktop_orchestrator
from infiniteprep.services.import_service import ImportService
from tests.fixtures.audio_factory import create_wav_fixture

TRACK_TAGS = {"artist": "Real Artist", "title": "Real Title"}


def import_one_track(repo: Repository, crate: Path) -> str:
    """Import one tagged WAV track and return its file id."""
    create_wav_fixture(crate / "track.wav", duration_sec=0.5, tags=TRACK_TAGS)
    report = ImportService(repo).import_paths([crate])
    return report.imported_files[0].id


def test_desktop_analyze_stores_no_mock_matches(tmp_path: Path) -> None:
    """Desktop Analyze runs local analysis and stores no made-up candidate matches."""
    repo = Repository(DatabaseManager(":memory:"))
    file_id = import_one_track(repo, tmp_path / "crate")

    summary = build_desktop_orchestrator(repo).process_files([file_id])

    assert summary.completed_files == 1
    assert repo.get_file_by_id(file_id).task_status == TaskStatus.COMPLETED
    assert repo.list_candidate_matches(file_id) == []
    assert repo.list_analysis_results(file_id)
