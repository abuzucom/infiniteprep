"""A version change marks the older analysis results as stale."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, KeyMode, PitchClass
from infiniteprep.domain.models import AnalysisResult
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.reconciliation import ReconciliationService
from infiniteprep.ui.detail_panel import format_analysis_row
from infiniteprep.ui.main_window import STALE_ANALYSIS_WARNING, load_library_snapshot
from tests.fixtures.audio_factory import create_wav_fixture

ANALYZER_NAME = "local_dsp"
FIRST_VERSION = "0.1.0"
SECOND_VERSION = "0.2.0"


@pytest.fixture
def library(tmp_path: Path) -> tuple[Repository, str]:
    """Import one track and return the repository with its file ID."""
    repo = Repository(DatabaseManager(":memory:"))
    path = create_wav_fixture(tmp_path / "track.wav", duration_sec=1.0)
    report = ImportService(repo).import_paths([path])
    return repo, report.imported_files[0].id


def store_result(repo: Repository, file_id: str, version: str, bpm: float) -> AnalysisResult:
    """Store one analysis result for the imported file."""
    result = AnalysisResult(
        id=f"ana-{version}",
        file_id=file_id,
        analyzer_name=ANALYZER_NAME,
        analyzer_version=version,
        configuration={},
        bpm=bpm,
        key_tonic=PitchClass.A,
        key_mode=KeyMode.MINOR,
        analysis_strength=0.9,
    )
    return repo.upsert_analysis_result(result)


def test_version_bump_marks_result_stale(library: tuple[Repository, str]) -> None:
    """A result from an older analyzer version turns stale when a new one arrives."""
    repo, file_id = library

    store_result(repo, file_id, FIRST_VERSION, 128.0)
    store_result(repo, file_id, SECOND_VERSION, 127.0)

    stored = {result.analyzer_version: result for result in repo.list_analysis_results(file_id)}
    assert stored[FIRST_VERSION].stale is True
    assert stored[SECOND_VERSION].stale is False
    assert len(repo.list_current_analysis_results(file_id)) == 1


def test_current_version_keeps_result_current(library: tuple[Repository, str]) -> None:
    """The results of the running analyzer version keep their values."""
    repo, file_id = library
    store_result(repo, file_id, FIRST_VERSION, 128.0)

    changed = repo.mark_analysis_results_stale(ANALYZER_NAME, FIRST_VERSION)

    assert changed == 0
    assert repo.list_analysis_results(file_id)[0].stale is False


def test_stale_result_makes_no_proposal(library: tuple[Repository, str]) -> None:
    """A stale result states no BPM and no key, so it makes no proposal."""
    repo, file_id = library
    store_result(repo, file_id, FIRST_VERSION, 128.0)
    repo.mark_analysis_results_stale(ANALYZER_NAME, SECOND_VERSION)

    ReconciliationService(repo).reconcile_file(file_id)

    assert repo.list_field_proposals(file_id, FieldName.BPM.value) == []
    assert repo.list_field_proposals(file_id, FieldName.KEY.value) == []


def test_stale_marker_reaches_library_snapshot(library: tuple[Repository, str]) -> None:
    """The library shows that the track needs a new analysis."""
    repo, file_id = library
    store_result(repo, file_id, FIRST_VERSION, 128.0)
    repo.mark_analysis_results_stale(ANALYZER_NAME, SECOND_VERSION)

    snapshot = load_library_snapshot(repo)

    assert snapshot.analysis_warnings[file_id] == [STALE_ANALYSIS_WARNING]


def test_detail_row_marks_stale_result() -> None:
    """The detail panel names a result that no longer states the current values."""
    assert format_analysis_row("128.0", "8A", stale=True).endswith("(outdated)")
    assert format_analysis_row("128.0", "8A") == "Detected: 128.0 BPM, key 8A"
