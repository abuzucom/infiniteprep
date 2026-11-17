"""Re-analysis stores fresh proposals, and a file status update can clear an old error."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    FieldName,
    KeyMode,
    PitchClass,
    ProposalReviewStatus,
    ReviewStatus,
    TaskStatus,
)
from infiniteprep.domain.models import AnalysisResult
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import JobOrchestrator
from infiniteprep.services.reconciliation import ReconciliationService
from tests.fixtures.audio_factory import create_wav_fixture

ANALYZER_NAME: str = "test_analyzer"
FIRST_VERSION: str = "0.1.0"
SECOND_VERSION: str = "0.2.0"
EARLIER: str = "2026-01-01T00:00:00+00:00"
LATER: str = "2026-01-02T00:00:00+00:00"
OLD_ERROR: str = "Processing failed (ValueError: bad file); retry or check provider settings."


class LibraryTestCase(unittest.TestCase):
    """Shared library setup with one imported file."""

    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        database = DatabaseManager(":memory:")
        self.addCleanup(database.close)
        self.repo = Repository(database)
        path = create_wav_fixture(Path(temp_dir.name) / "track.wav", duration_sec=1.0)
        report = ImportService(self.repo, MetadataTagAdapter()).import_paths([path])
        self.file_id = report.imported_files[0].id

    def store_analysis(self, result_id: str, *, bpm: float, version: str, analyzed_at: str) -> None:
        """Store one analysis result for the imported file."""
        self.repo.upsert_analysis_result(
            AnalysisResult(
                id=result_id,
                file_id=self.file_id,
                analyzer_name=ANALYZER_NAME,
                analyzer_version=version,
                configuration={},
                bpm=bpm,
                key_tonic=PitchClass.A,
                key_mode=KeyMode.MINOR,
                analysis_strength=0.9,
                analyzed_at=analyzed_at,
            )
        )

    def pending_bpm_values(self) -> list[str]:
        """Return the pending BPM proposal values of the imported file."""
        proposals = self.repo.list_field_proposals(self.file_id, FieldName.BPM.value)
        return [
            proposal.proposed_value
            for proposal in proposals
            if proposal.review_status == ProposalReviewStatus.PENDING
        ]


class ReanalysisProposalTests(LibraryTestCase):
    """A changed analysis replaces the pending analysis proposal."""

    def test_changed_bpm_for_the_same_analyzer_version_reconciles(self) -> None:
        service = ReconciliationService(self.repo)
        self.store_analysis("ana-1", bpm=128.0, version=FIRST_VERSION, analyzed_at=EARLIER)
        service.reconcile_file(self.file_id)
        self.store_analysis("ana-1", bpm=126.0, version=FIRST_VERSION, analyzed_at=LATER)

        service.reconcile_file(self.file_id)

        self.assertEqual(self.pending_bpm_values(), ["126.0"])

    def test_analyzer_version_bump_reconciles_with_the_newest_result(self) -> None:
        service = ReconciliationService(self.repo)
        self.store_analysis("ana-1", bpm=128.0, version=FIRST_VERSION, analyzed_at=EARLIER)
        service.reconcile_file(self.file_id)
        self.store_analysis("ana-2", bpm=127.0, version=SECOND_VERSION, analyzed_at=LATER)

        service.reconcile_file(self.file_id)

        self.assertEqual(self.pending_bpm_values(), ["127.0"])


class FileStatusTests(LibraryTestCase):
    """update_file_status changes only the named columns and can clear the error."""

    def mark_failed(self) -> None:
        """Record a failed processing attempt on the imported file."""
        self.repo.update_file_status(
            self.file_id,
            task_status=TaskStatus.FAILED,
            review_status=ReviewStatus.ERROR,
            error_message=OLD_ERROR,
        )

    def test_clear_error_removes_the_stored_message(self) -> None:
        self.mark_failed()

        self.repo.update_file_status(
            self.file_id, task_status=TaskStatus.COMPLETED, clear_error=True
        )

        asset = self.repo.get_file_by_id(self.file_id)
        assert asset is not None
        self.assertIsNone(asset.error_message)
        self.assertEqual(asset.task_status, TaskStatus.COMPLETED)
        self.assertEqual(asset.review_status, ReviewStatus.ERROR)

    def test_update_without_clear_error_keeps_the_message(self) -> None:
        self.mark_failed()

        self.repo.update_file_status(self.file_id, task_status=TaskStatus.PROCESSING)

        asset = self.repo.get_file_by_id(self.file_id)
        assert asset is not None
        self.assertEqual(asset.error_message, OLD_ERROR)
        self.assertEqual(asset.review_status, ReviewStatus.ERROR)

    def test_successful_processing_clears_the_old_error(self) -> None:
        self.mark_failed()

        summary = JobOrchestrator(repository=self.repo).process_files([self.file_id])

        asset = self.repo.get_file_by_id(self.file_id)
        assert asset is not None
        self.assertEqual(summary.completed_files, 1)
        self.assertEqual(asset.task_status, TaskStatus.COMPLETED)
        self.assertIsNone(asset.error_message)


if __name__ == "__main__":
    unittest.main()
