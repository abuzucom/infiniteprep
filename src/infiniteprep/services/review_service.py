"""Proposal review, manual editing, field locking, and bulk approval service (Section 7.E)."""

from __future__ import annotations

from typing import Final

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    ApprovalOrigin,
    FieldName,
    ProposalReviewStatus,
    ReviewStatus,
)
from infiniteprep.domain.models import ApprovedField, CandidateMatch, FieldProposal, utc_now_iso

CORE_IDENTITY_FIELDS: Final[frozenset[str]] = frozenset(
    {FieldName.ARTIST.value, FieldName.TITLE.value}
)
MAX_MANUAL_FIELD_LENGTH: Final[int] = 500
DEFAULT_BULK_APPROVE_MIN_SCORE: Final[float] = 0.85


class ReviewService:
    """Manages proposal acceptance, manual edits, field locks, and group propagation."""

    def __init__(self, repository: Repository) -> None:
        """Initialize ReviewService with a Repository."""
        self.repo = repository

    def accept_proposal(self, proposal_id: str, lock: bool = False) -> bool:
        """Accept a FieldProposal if the target field is not currently locked."""
        proposal = self.repo.get_field_proposal_by_id(proposal_id)
        if proposal is None:
            return False

        existing = self.repo.get_approved_field(proposal.file_id, proposal.field_name)
        if existing is not None and existing.locked:
            return False

        approved = ApprovedField(
            file_id=proposal.file_id,
            field_name=proposal.field_name,
            approved_value=proposal.proposed_value,
            approval_origin=ApprovalOrigin.PROPOSAL_ACCEPTED,
            approved_at=utc_now_iso(),
            locked=lock,
            provider_attribution={
                "provider": proposal.provider,
                "entity_id": proposal.provider_entity_id,
                "proposal_id": proposal.id,
            },
        )
        self.repo.upsert_approved_field(approved, respect_existing_lock=True)
        self.repo.update_proposal_status(proposal.id, ProposalReviewStatus.ACCEPTED)
        self._refresh_file_review_status(proposal.file_id)
        return True

    def reject_proposal(self, proposal_id: str) -> bool:
        """Mark a FieldProposal as REJECTED."""
        proposal = self.repo.get_field_proposal_by_id(proposal_id)
        if proposal is None:
            return False
        self.repo.update_proposal_status(proposal_id, ProposalReviewStatus.REJECTED)
        self._refresh_file_review_status(proposal.file_id)
        return True

    def set_manual_field_value(
        self,
        file_id: str,
        field_name: str,
        value: str,
        lock: bool = True,
    ) -> ApprovedField:
        """Set a manual field override and optionally lock it against future proposals."""
        cleaned = value.strip()
        if len(cleaned) > MAX_MANUAL_FIELD_LENGTH:
            raise ValueError(
                f"Field value for '{field_name}' exceeds maximum length "
                f"({MAX_MANUAL_FIELD_LENGTH} chars)."
            )
        approved = ApprovedField(
            file_id=file_id,
            field_name=field_name,
            approved_value=cleaned,
            approval_origin=ApprovalOrigin.MANUAL_EDIT,
            approved_at=utc_now_iso(),
            locked=lock,
            provider_attribution={"provider": "user_manual"},
        )
        saved = self.repo.upsert_approved_field(approved, respect_existing_lock=False)
        self._refresh_file_review_status(file_id)
        return saved

    def set_field_lock(self, file_id: str, field_name: str, locked: bool) -> None:
        """Explicitly lock or unlock an approved field on a file."""
        self.repo.set_field_lock(file_id, field_name, locked=locked)

    def bulk_approve_non_conflicting(
        self, file_ids: list[str], min_score: float = DEFAULT_BULK_APPROVE_MIN_SCORE
    ) -> int:
        """Approve fields where all pending proposals agree and meet min_score."""
        approved_count = 0
        for file_id in file_ids:
            proposals = self.repo.list_field_proposals(file_id)
            by_field = self._group_pending_proposals_by_field(proposals)
            for field_name, field_props in by_field.items():
                if self._try_auto_approve_field(file_id, field_name, field_props, min_score):
                    approved_count += 1
        return approved_count

    def _group_pending_proposals_by_field(
        self, proposals: list[FieldProposal]
    ) -> dict[str, list[FieldProposal]]:
        """Group non-rejected proposals by field_name."""
        grouped: dict[str, list[FieldProposal]] = {}
        for prop in proposals:
            if prop.review_status == ProposalReviewStatus.REJECTED:
                continue
            grouped.setdefault(prop.field_name, []).append(prop)
        return grouped

    def _try_auto_approve_field(
        self,
        file_id: str,
        field_name: str,
        field_props: list[FieldProposal],
        min_score: float,
    ) -> bool:
        """Approve top proposal if all distinct proposed values agree and score >= min_score."""
        existing = self.repo.get_approved_field(file_id, field_name)
        if existing is not None and existing.locked:
            return False

        distinct_values = {p.proposed_value.strip().lower() for p in field_props}
        if len(distinct_values) != 1:
            return False

        best = field_props[0]
        score = best.application_match_score
        if score is None:
            score = best.provider_confidence or 0.0
        if score < min_score:
            return False

        return self.accept_proposal(best.id, lock=False)

    def approve_release_for_source_group(
        self, source_group_id: str, candidate: CandidateMatch
    ) -> int:
        """Propagate shared release-level fields from a CandidateMatch to a SourceGroup."""
        files = self.repo.list_files(source_group_id=source_group_id)
        rel = candidate.release_details
        shared_fields: list[tuple[str, str | None]] = [
            (FieldName.RELEASE_TITLE.value, rel.release_title),
            (FieldName.LABEL.value, rel.label),
            (FieldName.CATALOG_NUMBER.value, rel.catalog_number),
            (FieldName.YEAR.value, rel.year),
            (FieldName.GENRE.value, rel.genre),
        ]
        updated_files = 0
        for asset in files:
            if self._apply_shared_release_fields(asset.id, candidate, shared_fields):
                updated_files += 1
                self._refresh_file_review_status(asset.id)
        return updated_files

    def _apply_shared_release_fields(
        self,
        file_id: str,
        candidate: CandidateMatch,
        shared_fields: list[tuple[str, str | None]],
    ) -> bool:
        """Write non-empty release fields onto unlocked fields of a single file."""
        wrote_any = False
        for field_name, val in shared_fields:
            if not val:
                continue
            existing = self.repo.get_approved_field(file_id, field_name)
            if existing is not None and existing.locked:
                continue
            approved = ApprovedField(
                file_id=file_id,
                field_name=field_name,
                approved_value=val,
                approval_origin=ApprovalOrigin.PROPOSAL_ACCEPTED,
                approved_at=utc_now_iso(),
                locked=False,
                provider_attribution={
                    "provider": candidate.provider,
                    "entity_id": candidate.provider_entity_id,
                    "group_propagation": True,
                },
            )
            self.repo.upsert_approved_field(approved, respect_existing_lock=True)
            wrote_any = True
        return wrote_any

    def _refresh_file_review_status(self, file_id: str) -> None:
        """Recompute ReviewStatus for a FileAsset based on proposals and approved fields."""
        asset = self.repo.get_file_by_id(file_id)
        if asset is None or asset.review_status == ReviewStatus.WRITTEN:
            return
        approved_map = self.repo.get_approved_fields_map(file_id)
        proposals = self.repo.list_field_proposals(file_id)
        if not approved_map and not proposals:
            return
        if not approved_map and proposals:
            self.repo.update_file_status(file_id, review_status=ReviewStatus.PROPOSED)
            return
        has_core = CORE_IDENTITY_FIELDS.issubset(approved_map.keys())
        new_status = ReviewStatus.READY_TO_WRITE if has_core else ReviewStatus.PARTIALLY_APPROVED
        self.repo.update_file_status(file_id, review_status=new_status)
