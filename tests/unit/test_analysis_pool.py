"""Behavioral tests for the analysis pool: order, cancel, and a crashed worker."""

from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.config.settings import SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services import analysis_pool
from infiniteprep.services.analysis_pool import (
    WORKER_CRASH_MESSAGE,
    AnalysisPool,
    resolve_worker_count,
)
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import MISSING_FILE_MESSAGE, JobOrchestrator
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.worker_analyzers import (
    COUNTER_ENVIRONMENT_VARIABLE,
    CRASHING_FILE_NAME,
    CrashAndSlowAnalyzer,
    CrashingAnalyzer,
    FailingSlowAnalyzer,
    SlowAnalyzer,
)

TRACK_NAMES = ("a_first.wav", CRASHING_FILE_NAME, "c_last.wav")
# The batch must end before the slow worker finishes its file.
PROMPT_STOP_SECONDS = 10.0


@pytest.fixture
def crate(tmp_path: Path) -> Path:
    """Build three WAV files in a folder."""
    folder = tmp_path / "crate"
    for name in TRACK_NAMES:
        create_wav_fixture(folder / name, duration_sec=2.0)
    return folder


@pytest.fixture
def imported_ids(crate: Path) -> tuple[Repository, list[str]]:
    """Import the three files and return the repository with their file IDs."""
    repo = Repository(DatabaseManager(":memory:"))
    report = ImportService(repo).import_paths([crate])
    by_name = {Path(asset.current_path).name: asset.id for asset in report.imported_files}
    return repo, [by_name[name] for name in TRACK_NAMES]


def test_pool_returns_results_in_order(crate: Path) -> None:
    """Every submitted file comes back as one result that names the analyzer version."""
    paths = [crate / name for name in TRACK_NAMES]

    with AnalysisPool(worker_count=1) as pool:
        for index, path in enumerate(paths):
            pool.submit(path, f"file-{index}")
        outcomes = [pool.next_outcome() for _ in paths]

    assert [outcome.file_id for outcome in outcomes] == ["file-0", "file-1", "file-2"]
    assert all(outcome.result is not None for outcome in outcomes)
    assert all(outcome.failed is False for outcome in outcomes)
    versions = {outcome.result.analyzer_version for outcome in outcomes if outcome.result}
    assert versions == {"0.1.0"}


def test_pool_cancel_stops_worker_processes(crate: Path) -> None:
    """A cancel stops the worker processes at once and returns no result."""
    children_before = len(multiprocessing.active_children())

    with AnalysisPool(analyzer_factory=SlowAnalyzer, worker_count=1) as pool:
        pool.submit(crate / TRACK_NAMES[0], "file-0")
        outcome = pool.next_outcome(lambda: True)
        deadline = time.monotonic() + 5.0
        while (
            len(multiprocessing.active_children()) > children_before and time.monotonic() < deadline
        ):
            time.sleep(0.05)

    assert outcome.cancelled is True
    assert outcome.result is None
    assert len(multiprocessing.active_children()) == children_before


def test_pool_retries_crashed_file(crate: Path, tmp_path: Path, monkeypatch: Any) -> None:
    """A worker that dies costs one file. The other files keep their results."""
    counter = tmp_path / "calls.txt"
    monkeypatch.setenv(COUNTER_ENVIRONMENT_VARIABLE, str(counter))
    paths = [crate / name for name in TRACK_NAMES]

    with AnalysisPool(analyzer_factory=CrashingAnalyzer, worker_count=1) as pool:
        for index, path in enumerate(paths):
            pool.submit(path, f"file-{index}")
        outcomes = [pool.next_outcome() for _ in paths]

    by_id = {outcome.file_id: outcome for outcome in outcomes}
    assert by_id["file-1"].failed is True
    assert by_id["file-1"].error == WORKER_CRASH_MESSAGE
    assert by_id["file-0"].result is not None
    assert by_id["file-2"].result is not None
    calls = counter.read_text(encoding="utf-8").split()
    assert calls.count(TRACK_NAMES[0]) == 1
    assert calls.count(CRASHING_FILE_NAME) == 2
    assert calls.count(TRACK_NAMES[2]) == 1


def test_cancel_reaches_the_crash_recovery(crate: Path, tmp_path: Path, monkeypatch: Any) -> None:
    """A cancel request reaches the recovery path that runs after a worker died."""
    counter = tmp_path / "calls.txt"
    monkeypatch.setenv(COUNTER_ENVIRONMENT_VARIABLE, str(counter))

    def should_cancel() -> bool:
        """Report the cancel once the slow file runs in the recovery."""
        if not counter.exists():
            return False
        return TRACK_NAMES[2] in counter.read_text(encoding="utf-8")

    with AnalysisPool(analyzer_factory=CrashAndSlowAnalyzer, worker_count=1) as pool:
        pool.submit(crate / CRASHING_FILE_NAME, "file-0")
        pool.submit(crate / TRACK_NAMES[2], "file-1")
        outcome = pool.next_outcome(should_cancel)

    assert outcome.cancelled is True
    assert outcome.result is None


def test_timed_out_file_fails(crate: Path, monkeypatch: Any) -> None:
    """A file that takes longer than the time limit ends as one failed file."""
    monkeypatch.setattr(analysis_pool, "ANALYSIS_TIMEOUT_SECONDS", 1.0)

    with AnalysisPool(analyzer_factory=SlowAnalyzer, worker_count=1) as pool:
        pool.submit(crate / TRACK_NAMES[0], "file-0")
        outcome = pool.next_outcome()

    assert outcome.failed is True
    assert outcome.error == analysis_pool.ANALYSIS_TIMEOUT_MESSAGE


def test_worker_count_follows_cpu_limit() -> None:
    """The worker count never passes the processor count or the settings cap."""
    cpu_count = os.cpu_count() or 1

    assert resolve_worker_count(1) == 1
    assert resolve_worker_count(99) == min(4, cpu_count)
    assert resolve_worker_count(None) == min(4, cpu_count)


def test_settings_store_worker_count(tmp_path: Path) -> None:
    """The stored worker count reaches the pool."""
    manager = SettingsManager(tmp_path / "settings.json")

    settings = manager.update(analysis_workers=2)

    assert settings.analysis_workers == 2
    assert resolve_worker_count(settings.analysis_workers) == min(2, os.cpu_count() or 1)


def test_settings_refuse_bad_worker_count(tmp_path: Path) -> None:
    """A worker count outside the supported range is refused."""
    manager = SettingsManager(tmp_path / "settings.json")

    with pytest.raises(ValueError):
        manager.update(analysis_workers=9)


def test_missing_file_counts_as_failed(imported_ids: Any) -> None:
    """A requested file that no longer exists counts as one failed file."""
    repo, file_ids = imported_ids
    orchestrator = JobOrchestrator(repository=repo)

    with AnalysisPool(worker_count=1) as pool:
        summary = orchestrator.process_files_continuing(
            [*file_ids, "missing-file-id"], analysis_pool=pool
        )

    assert summary.completed_files == len(file_ids)
    assert summary.failed_files == 1
    assert summary.errors["missing-file-id"] == MISSING_FILE_MESSAGE


class RecordingPool(AnalysisPool):
    """Pool that records every submitted file."""

    def __init__(self, worker_count: int | None = None) -> None:
        """Start the pool without a worker process."""
        super().__init__(worker_count=worker_count)
        self.submitted: list[str] = []

    def submit(self, path: Path, file_id: str) -> None:
        """Record the file and send it to the pool."""
        self.submitted.append(file_id)
        super().submit(path, file_id)


def test_batch_fills_the_worker_window(imported_ids: Any) -> None:
    """The batch keeps the worker window full. The waiting analyses never pass the limit."""
    repo, file_ids = imported_ids
    orchestrator = JobOrchestrator(repository=repo)
    pool = RecordingPool(worker_count=2)
    waiting: list[int] = []
    consumed = 0

    def record(done: int, total: int, path: str) -> None:
        """Record the analyses that wait for a worker at each progress report."""
        nonlocal consumed
        waiting.append(len(pool.submitted) - consumed)
        consumed += 1

    summary = orchestrator.process_files_continuing(
        ["missing-file-id", *file_ids],
        progress=record,
        analysis_pool=pool,
    )

    assert waiting == [2, 2, 1]
    assert summary.failed_files == 1
    assert summary.completed_files == len(file_ids)


def test_batch_reports_progress_per_file(imported_ids: Any) -> None:
    """The queued batch reports the progress of every file it analyzes."""
    repo, file_ids = imported_ids
    orchestrator = JobOrchestrator(repository=repo)
    reports: list[tuple[int, int, str]] = []

    with AnalysisPool(worker_count=1) as pool:
        summary = orchestrator.process_files_continuing(
            file_ids,
            progress=lambda done, total, path: reports.append((done, total, path)),
            analysis_pool=pool,
        )

    assert summary.completed_files == len(file_ids)
    assert summary.failed_files == 0
    assert [report[0] for report in reports] == [1, 2, 3]
    assert all(report[1] == len(file_ids) for report in reports)
    stored = repo.list_analysis_results(file_ids[0])
    assert len(stored) == 1
    assert stored[0].analyzer_name == "local_dsp"


def test_stop_on_failure_ends_promptly(imported_ids: Any) -> None:
    """A failed file stops the batch at once. The prefetched analyses do not delay it."""
    repo, file_ids = imported_ids
    orchestrator = JobOrchestrator(repository=repo)
    Path(repo.get_file_by_id(file_ids[0]).current_path).unlink()
    started = time.monotonic()

    with AnalysisPool(analyzer_factory=FailingSlowAnalyzer, worker_count=2) as pool:
        summary = orchestrator.process_files_continuing(
            file_ids, stop_on_failure=True, analysis_pool=pool
        )
    elapsed = time.monotonic() - started

    assert summary.stopped_early is True
    assert summary.failed_files == 1
    assert summary.completed_files == 0
    assert elapsed < PROMPT_STOP_SECONDS
