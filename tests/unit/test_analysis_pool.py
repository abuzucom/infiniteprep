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
from infiniteprep.services.analysis_pool import (
    WORKER_CRASH_MESSAGE,
    AnalysisPool,
    resolve_worker_count,
)
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import JobOrchestrator
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.worker_analyzers import (
    CRASHING_FILE_NAME,
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


def test_pool_retries_crashed_file(crate: Path) -> None:
    """A worker that dies costs one file. The other files keep their results."""
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
