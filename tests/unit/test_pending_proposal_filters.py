"""Rejected, superseded, and accepted proposals stay out of pending-only decisions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    FieldName,
    KeyDisplayNotation,
    KeyMode,
    PitchClass,
    ProposalReviewStatus,
    format_canonical_key,
)
from infiniteprep.domain.metadata_values import top_proposal_values
from infiniteprep.domain.models import FieldProposal
from infiniteprep.domain.playlist_order import order_tracks
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from tests.fixtures.audio_factory import create_wav_fixture

HIGH_SCORE: float = 0.99
GOOD_SCORE: float = 0.95
LOW_SCORE: float = 0.50
A_MINOR: str = format_canonical_key(PitchClass.A, KeyMode.MINOR, KeyDisplayNotation.STANDARD)


class ProposalFilterTestCase(unittest.TestCase):
    """Shared library setup with imported files and stored proposals."""

    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)
        database = DatabaseManager(":memory:")
        self.addCleanup(database.close)
        self.repo = Repository(database)

    def import_track(self, name: str) -> str:
        """Import one untagged WAV file and return its asset id."""
        path = create_wav_fixture(self.root / name, duration_sec=0.5)
        report = ImportService(self.repo, MetadataTagAdapter()).import_paths([path])
        return report.imported_files[0].id

    def store_proposal(
        self,
        file_id: str,
        field_name: str,
        value: str,
        *,
        score: float,
        status: ProposalReviewStatus = ProposalReviewStatus.PENDING,
        provider: str = "provider_a",
    ) -> FieldProposal:
        """Store one proposal and return it."""
        proposal = FieldProposal(
            id=f"{file_id}:{field_name}:{provider}:{value}",
            file_id=file_id,
            field_name=field_name,
            proposed_value=value,
            provider=provider,
            provider_entity_id=f"entity-{value}",
            provider_confidence=score,
            application_match_score=score,
            review_status=status,
        )
        return self.repo.upsert_field_proposal(proposal)


class BulkApproveTests(ProposalFilterTestCase):
    """Bulk approve reads pending proposals only."""

    def test_superseded_proposal_does_not_block_the_pending_value(self) -> None:
        """A higher superseded proposal leaves bulk approve free to approve the pending one."""
        file_id = self.import_track("a.wav")
        self.store_proposal(file_id, FieldName.LABEL.value, "Tribal America", score=GOOD_SCORE)
        self.store_proposal(
            file_id,
            FieldName.LABEL.value,
            "Other Label",
            score=HIGH_SCORE,
            status=ProposalReviewStatus.SUPERSEDED,
            provider="provider_b",
        )

        approved_count = ReviewService(self.repo).bulk_approve_non_conflicting([file_id])

        self.assertEqual(approved_count, 1)
        approved = self.repo.get_approved_field(file_id, FieldName.LABEL.value)
        assert approved is not None
        self.assertEqual(approved.approved_value, "Tribal America")

    def test_second_run_approves_nothing_new(self) -> None:
        """A repeat bulk approve finds no pending proposal left to approve."""
        file_id = self.import_track("a.wav")
        self.store_proposal(file_id, FieldName.LABEL.value, "Tribal America", score=GOOD_SCORE)
        service = ReviewService(self.repo)

        first_count = service.bulk_approve_non_conflicting([file_id])
        second_count = service.bulk_approve_non_conflicting([file_id])

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)


class TopProposalValueTests(ProposalFilterTestCase):
    """The effective top proposal skips rejected and superseded rows."""

    def test_rejected_and_superseded_values_are_skipped(self) -> None:
        """The top value comes from the pending proposal despite higher scored rejected rows."""
        file_id = self.import_track("a.wav")
        self.store_proposal(
            file_id,
            FieldName.BPM.value,
            "100.0",
            score=HIGH_SCORE,
            status=ProposalReviewStatus.REJECTED,
        )
        self.store_proposal(
            file_id,
            FieldName.BPM.value,
            "110.0",
            score=GOOD_SCORE,
            status=ProposalReviewStatus.SUPERSEDED,
        )
        self.store_proposal(file_id, FieldName.BPM.value, "128.0", score=LOW_SCORE)

        values = top_proposal_values(self.repo.list_field_proposals(file_id))

        self.assertEqual(values, {FieldName.BPM.value: "128.0"})

    def test_playlist_order_ignores_a_rejected_bpm(self) -> None:
        """Playlist ordering by BPM uses the pending BPM instead of a rejected one."""
        slow_id = self.import_track("slow.wav")
        fast_id = self.import_track("fast.wav")
        self.store_proposal(slow_id, FieldName.BPM.value, "120.0", score=GOOD_SCORE)
        self.store_proposal(slow_id, FieldName.KEY.value, A_MINOR, score=GOOD_SCORE)
        self.store_proposal(
            fast_id,
            FieldName.BPM.value,
            "100.0",
            score=HIGH_SCORE,
            status=ProposalReviewStatus.REJECTED,
        )
        self.store_proposal(fast_id, FieldName.BPM.value, "130.0", score=LOW_SCORE)
        self.store_proposal(fast_id, FieldName.KEY.value, A_MINOR, score=GOOD_SCORE)

        ordered = order_tracks(self.repo, [fast_id, slow_id], bpm_first=True)

        self.assertEqual(ordered, [slow_id, fast_id])


if __name__ == "__main__":
    unittest.main()
