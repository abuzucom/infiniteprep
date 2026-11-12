"""Behavioral tests for the queued Analyze path: per-file failure, stop, and cancel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.analyzers.mock_analyzer import MockAudioAnalyzerAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import TaskStatus
from infiniteprep.domain.models import AnalysisResult
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import JobOrchestrator
from tests.fixtures.audio_factory import create_wav_fixture

TRACK_NAMES = ("a_first.wav", "b_broken.wav", "c_last.wav")
BROKEN_NAME = "b_broken.wav"


class AnalyzerDefectError(Exception):
    """An error type that EXPECTED_JOB_ERRORS does not list."""


class BrokenFileAnalyzer(MockAudioAnalyzerAdapter):
    """Raise an error type outside EXPECTED_JOB_ERRORS for one named file."""

    def analyze(
        self, file_id_or_path: str | Path, maybe_path: Path | str | None = None
    ) -> AnalysisResult:
        """Fail on the broken file and analyze every other file as usual."""
        if Path(file_id_or_path).name == BROKEN_NAME:
            raise AnalyzerDefectError("analyzer invariant broke")
        return super().analyze(file_id_or_path, maybe_path)


@pytest.fixture
def analyze_parts(tmp_path: Path) -> tuple[Repository, JobOrchestrator, list[str]]:
    """Import three tracks. The analyzer fails on the middle one."""
    repo = Repository(DatabaseManager(":memory:"))
    for name in TRACK_NAMES:
        create_wav_fixture(tmp_path / "crate" / name, duration_sec=0.5)
    report = ImportService(repo).import_paths([tmp_path / "crate"])
    by_name = {Path(asset.current_path).name: asset.id for asset in report.imported_files}
    file_ids = [by_name[name] for name in TRACK_NAMES]
    orchestrator = JobOrchestrator(repository=repo, audio_analyzer=BrokenFileAnalyzer())
    return repo, orchestrator, file_ids


def test_a_failed_file_lets_the_batch_continue_by_default(analyze_parts: Any) -> None:
    """Any error type on one file marks that file failed, and the later files still run."""
    repo, orchestrator, file_ids = analyze_parts

    summary = orchestrator.process_files_continuing(file_ids)

    assert summary.completed_files == 2
    assert summary.failed_files == 1
    assert summary.stopped_early is False
    assert list(summary.errors) == [file_ids[1]]
    assert "AnalyzerDefectError" in summary.errors[file_ids[1]]
    statuses = [repo.get_file_by_id(file_id).task_status for file_id in file_ids]
    assert statuses == [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.COMPLETED]


def test_process_files_still_propagates_an_unlisted_error(analyze_parts: Any) -> None:
    """The earlier method keeps its contract: an unlisted error type ends the batch."""
    _repo, orchestrator, file_ids = analyze_parts

    with pytest.raises(AnalyzerDefectError, match="analyzer invariant broke"):
        orchestrator.process_files(file_ids)


def test_stop_on_failure_ends_the_batch_at_the_first_failed_file(analyze_parts: Any) -> None:
    """With the stop rule on, files after the failed one stay untouched."""
    repo, orchestrator, file_ids = analyze_parts

    summary = orchestrator.process_files_continuing(file_ids, stop_on_failure=True)

    assert summary.completed_files == 1
    assert summary.failed_files == 1
    assert summary.stopped_early is True
    assert repo.get_file_by_id(file_ids[2]).task_status == TaskStatus.IDLE
    assert repo.list_analysis_results(file_ids[2]) == []


def test_cancel_stops_before_the_next_file_and_keeps_finished_files(analyze_parts: Any) -> None:
    """A cancel request after the first file keeps its results and skips the rest."""
    repo, orchestrator, file_ids = analyze_parts
    reconciled: list[str] = []

    summary = orchestrator.process_files_continuing(
        file_ids,
        post_process=reconciled.append,
        should_cancel=lambda: bool(reconciled),
    )

    assert summary.completed_files == 1
    assert summary.failed_files == 0
    assert summary.stopped_early is True
    assert reconciled == [file_ids[0]]
    assert len(repo.list_analysis_results(file_ids[0])) == 1
    assert repo.get_file_by_id(file_ids[1]).task_status == TaskStatus.IDLE


def test_progress_and_post_process_cover_each_file(analyze_parts: Any) -> None:
    """progress names each file before it runs. post_process skips the failed file."""
    repo, orchestrator, file_ids = analyze_parts
    seen: list[tuple[int, int, str]] = []
    reconciled: list[str] = []

    orchestrator.process_files_continuing(
        file_ids,
        post_process=reconciled.append,
        progress=lambda done, total, path: seen.append((done, total, Path(path).name)),
    )

    assert seen == [(1, 3, TRACK_NAMES[0]), (2, 3, TRACK_NAMES[1]), (3, 3, TRACK_NAMES[2])]
    assert reconciled == [file_ids[0], file_ids[2]]
    assert repo.get_file_by_id(file_ids[1]).error_message is not None


def test_a_failing_post_process_counts_as_a_failed_file(analyze_parts: Any) -> None:
    """An error raised after processing, such as in reconciliation, fails only that file."""
    _repo, orchestrator, file_ids = analyze_parts

    def refuse_first(file_id: str) -> None:
        if file_id == file_ids[0]:
            raise KeyError("reconcile failed")

    summary = orchestrator.process_files_continuing(file_ids, post_process=refuse_first)

    assert summary.completed_files == 1
    assert summary.failed_files == 2
    assert set(summary.errors) == {file_ids[0], file_ids[1]}
