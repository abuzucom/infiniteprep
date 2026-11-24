"""Tests for the plain status labels in the library and the detail panel (#342 item 12)."""

from __future__ import annotations

from infiniteprep.domain.enums import ReviewStatus
from infiniteprep.ui.library_columns import format_status_cell, format_status_label

EXPECTED_LABELS: dict[ReviewStatus, str] = {
    ReviewStatus.UNPROCESSED: "Not analyzed",
    ReviewStatus.PROCESSING: "Analyzing",
    ReviewStatus.PROPOSED: "Has suggestions",
    ReviewStatus.READY_FOR_REVIEW: "Needs review",
    ReviewStatus.CONFLICTING_CANDIDATES: "Conflicting matches",
    ReviewStatus.PARTIALLY_IDENTIFIED: "Partly matched",
    ReviewStatus.PARTIALLY_APPROVED: "Partly approved",
    ReviewStatus.NO_MATCH: "No match",
    ReviewStatus.APPROVED: "Approved",
    ReviewStatus.READY_TO_WRITE: "Ready to save",
    ReviewStatus.APPLIED: "Applied",
    ReviewStatus.WRITTEN: "Saved",
    ReviewStatus.ERROR: "Failed",
}


def test_every_status_has_its_plain_label() -> None:
    """Each stored status shows its approved plain label."""
    assert {status: format_status_label(status) for status in ReviewStatus} == EXPECTED_LABELS


def test_labels_stay_unique() -> None:
    """No two statuses share a label, so a label maps back to one stored value."""
    labels = [format_status_label(status) for status in ReviewStatus]

    assert len(set(labels)) == len(labels)


def test_cell_reads_stored_values_and_keeps_unknown_ones() -> None:
    """A stored value shows its label. An unknown value shows as stored."""
    assert format_status_cell("written") == "Saved"
    assert format_status_cell("not_a_status") == "not_a_status"
