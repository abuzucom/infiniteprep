"""DJ-aware reconciliation service implementing Rules 1-10 and Vinyl Pitch Drift detection."""

from __future__ import annotations

import uuid
from typing import Any, Final

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    EntityType,
    FieldName,
    KeyDisplayNotation,
    ProposalReviewStatus,
    ReviewStatus,
    format_canonical_key,
)
from infiniteprep.domain.models import (
    AnalysisResult,
    ApprovedField,
    CandidateMatch,
    FieldProposal,
    FileAsset,
)
from infiniteprep.domain.source_priority import (
    USER_VALUE_ORIGINS,
    outranks,
    priority_rank,
    provider_may_supply,
)
from infiniteprep.services.matching_engine import MatchingEngine, parse_dj_title_and_mix

MIN_DURATION_FOR_PITCH_DRIFT_SEC: Final[float] = 10.0
PERCENT_MULTIPLIER: Final[float] = 100.0
PITCH_DRIFT_MIN_PERCENT: Final[float] = 1.8
PITCH_DRIFT_MAX_PERCENT: Final[float] = 8.5
RPM_33_3: Final[float] = 33.333
RPM_45: Final[float] = 45.0
RPM_RATIO_TOLERANCE: Final[float] = 0.06
DEFAULT_ANALYSIS_CONFIDENCE: Final[float] = 0.85


def detect_vinyl_pitch_drift(
    *,
    file_duration_sec: float | None,
    catalog_duration_sec: float | None,
    analyzed_bpm: float | None = None,
) -> str | None:
    """Detect turntable pitch offset (1.8% to 8.5%) or 33.3 vs 45 RPM speed mismatch."""
    if (
        file_duration_sec is None
        or catalog_duration_sec is None
        or file_duration_sec <= MIN_DURATION_FOR_PITCH_DRIFT_SEC
        or catalog_duration_sec <= MIN_DURATION_FOR_PITCH_DRIFT_SEC
    ):
        return None
    speed_ratio = catalog_duration_sec / file_duration_sec
    percent_shift = (speed_ratio - 1.0) * PERCENT_MULTIPLIER
    if PITCH_DRIFT_MIN_PERCENT <= abs(percent_shift) <= PITCH_DRIFT_MAX_PERCENT:
        bpm_note = ""
        # Both durations passed the positive minimum above, so speed_ratio is positive.
        if analyzed_bpm:
            zero_pitch_bpm = round(analyzed_bpm / speed_ratio, 1)
            bpm_note = f"; estimated 0% pitch BPM ~ {zero_pitch_bpm}"
        return (
            f"Vinyl Pitch Drift Warning: File duration ({file_duration_sec:.1f}s) vs catalog "
            f"({catalog_duration_sec:.1f}s) suggests turntable pitch shift of "
            f"{percent_shift:+.1f}%{bpm_note}."
        )
    if (
        abs(speed_ratio - (RPM_45 / RPM_33_3)) <= RPM_RATIO_TOLERANCE
        or abs(speed_ratio - (RPM_33_3 / RPM_45)) <= RPM_RATIO_TOLERANCE
    ):
        return (
            "Vinyl Speed Mismatch Warning: Duration ratio matches 33 1/3 vs 45 RPM "
            "turntable speed error."
        )
    return None


class ReconciliationService:
    """Synthesizes CandidateMatches and AnalysisResults into FieldProposals (Section 8)."""

    def __init__(
        self,
        repository: Repository,
        matching_engine: MatchingEngine | None = None,
        reasoning_adapter: Any | None = None,
    ) -> None:
        """Initialize ReconciliationService with Repository, MatchingEngine, and optional LLM."""
        self.repo = repository
        self.matching = matching_engine or MatchingEngine()
        self.reasoning_adapter = reasoning_adapter

    def reconcile_file(self, file_id: str) -> list[FieldProposal]:
        """Reconcile all candidates and local analysis results for a FileAsset."""
        asset = self.repo.get_file_by_id(file_id)
        if asset is None or asset.suspected_multitrack:
            return []

        candidates = self.repo.list_candidate_matches(file_id)
        analyses = self.repo.list_analysis_results(file_id)
        generated: list[FieldProposal] = []

        for candidate in candidates:
            app_score = self.matching.compute_application_match_score(asset, candidate)
            generated.extend(self._proposals_from_candidate(asset, candidate, app_score))
            self._check_and_attach_pitch_drift(asset, candidate, analyses)

        analysis_proposals: list[FieldProposal] = []
        for analysis in self._latest_analysis_per_analyzer(analyses):
            analysis_proposals.extend(self._proposals_from_analysis(asset.id, analysis))
        generated.extend(analysis_proposals)

        kept = self._apply_source_priority(asset.id, generated)
        for proposal in kept:
            self.repo.upsert_field_proposal(proposal)
        self._supersede_stale_analysis_proposals(asset.id, analysis_proposals)
        self._supersede_lower_priority_proposals(asset.id, kept)

        self._update_review_status_after_reconcile(asset.id, kept)
        return self.repo.list_field_proposals(asset.id)

    def _latest_analysis_per_analyzer(self, analyses: list[AnalysisResult]) -> list[AnalysisResult]:
        """Return the newest result of each analyzer.

        The repository lists the results newest first. An analyzer version change leaves the
        older result in the table. Only the newest result states the current BPM and key.
        """
        latest: dict[str, AnalysisResult] = {}
        for analysis in analyses:
            latest.setdefault(analysis.analyzer_name, analysis)
        return list(latest.values())

    def _supersede_stale_analysis_proposals(
        self, file_id: str, current: list[FieldProposal]
    ) -> None:
        """Mark the pending proposals an analyzer no longer states as superseded.

        A re-analysis can change the BPM or the key. The earlier value stays in the table
        under its own row. It must not compete with the current value of the same analyzer.
        """
        # A row stored before this run keeps its own ID, so the unique key identifies it.
        current_keys = {self._proposal_key(proposal) for proposal in current}
        fields_by_provider = {(proposal.provider, proposal.field_name) for proposal in current}
        for stored in self.repo.list_field_proposals(file_id):
            if stored.review_status != ProposalReviewStatus.PENDING:
                continue
            if self._proposal_key(stored) in current_keys:
                continue
            if (stored.provider, stored.field_name) in fields_by_provider:
                self.repo.update_proposal_status(stored.id, ProposalReviewStatus.SUPERSEDED)

    @staticmethod
    def _proposal_key(proposal: FieldProposal) -> tuple[str, str, str, str]:
        """Return the columns that identify one stored proposal of a file."""
        return (
            proposal.field_name,
            proposal.proposed_value,
            proposal.provider,
            proposal.provider_entity_id,
        )

    def _apply_source_priority(
        self, file_id: str, proposals: list[FieldProposal]
    ) -> list[FieldProposal]:
        """Keep only the proposals of the highest ranked source for each ordered field.

        A field that the priority table does not list keeps every proposal. A
        provider that the table does not rank never loses a proposal and never
        wins one.
        """
        approved = self.repo.get_approved_fields_map(file_id)
        kept: list[FieldProposal] = []
        for proposal in proposals:
            if self._is_suppressed(proposal, proposals, approved):
                continue
            kept.append(proposal)
        return kept

    def _is_suppressed(
        self,
        proposal: FieldProposal,
        proposals: list[FieldProposal],
        approved: dict[str, ApprovedField],
    ) -> bool:
        """Return whether a higher ranked source or an approved value already supplied the field."""
        field_name = proposal.field_name
        if priority_rank(field_name, proposal.provider) is None:
            return False

        approved_field = approved.get(field_name)
        if approved_field is not None:
            if approved_field.approval_origin in USER_VALUE_ORIGINS:
                return True
            approved_provider = str(approved_field.provider_attribution.get("provider", ""))
            if outranks(field_name, approved_provider, proposal.provider):
                return True

        for other in proposals:
            if other.id == proposal.id or other.field_name != field_name:
                continue
            if outranks(field_name, other.provider, proposal.provider):
                return True
        return False

    def _supersede_lower_priority_proposals(self, file_id: str, kept: list[FieldProposal]) -> None:
        """Mark the stored proposals of a lower ranked source as superseded.

        The comparison covers every stored proposal for the file. A stored
        proposal from a lower ranked source goes superseded when a higher
        ranked source holds the field. The source may hold the field through an
        approved value or through a proposal from this run. A value the owner
        set holds the field on its own.
        """
        winners, owner_fields = self._field_owners(file_id, kept)
        if not winners and not owner_fields:
            return
        for stored in self.repo.list_field_proposals(file_id):
            if stored.review_status != ProposalReviewStatus.PENDING:
                continue
            if stored.field_name in owner_fields:
                self.repo.update_proposal_status(stored.id, ProposalReviewStatus.SUPERSEDED)
                continue
            winner = winners.get(stored.field_name)
            if winner is None:
                continue
            if outranks(stored.field_name, winner, stored.provider):
                self.repo.update_proposal_status(stored.id, ProposalReviewStatus.SUPERSEDED)

    def _field_owners(
        self, file_id: str, kept: list[FieldProposal]
    ) -> tuple[dict[str, str], set[str]]:
        """Return the source that holds each ordered field and the fields the owner set."""
        winners: dict[str, str] = {}
        owner_fields: set[str] = set()
        for field_name, approved_field in self.repo.get_approved_fields_map(file_id).items():
            if approved_field.approval_origin in USER_VALUE_ORIGINS:
                owner_fields.add(field_name)
                continue
            provider = str(approved_field.provider_attribution.get("provider", ""))
            if priority_rank(field_name, provider) is None:
                continue
            current = winners.get(field_name)
            if current is None or outranks(field_name, provider, current):
                winners[field_name] = provider
        for proposal in kept:
            if priority_rank(proposal.field_name, proposal.provider) is None:
                continue
            current = winners.get(proposal.field_name)
            if current is None or outranks(proposal.field_name, proposal.provider, current):
                winners[proposal.field_name] = proposal.provider
        return (winners, owner_fields)

    def _proposals_from_candidate(
        self, asset: FileAsset, candidate: CandidateMatch, app_score: float
    ) -> list[FieldProposal]:
        """Extract recording-level and release-level FieldProposals from a CandidateMatch."""
        proposals: list[FieldProposal] = []
        base_title, parsed_mix, parsed_remixer = parse_dj_title_and_mix(candidate.title or "")
        effective_title = base_title or candidate.title
        effective_mix = candidate.mix_name or parsed_mix
        effective_remixer = candidate.remixer or parsed_remixer

        recording_fields: list[tuple[str, str | None]] = [
            (FieldName.ARTIST.value, candidate.artist),
            (FieldName.TITLE.value, effective_title),
            (FieldName.MIX_NAME.value, effective_mix),
            (FieldName.REMIXER.value, effective_remixer),
        ]
        for f_name, value in recording_fields:
            if value and provider_may_supply(f_name, candidate.provider):
                proposals.append(self._make_proposal(asset.id, f_name, value, candidate, app_score))

        # Rule 1 & 2: Release-specific fields come only from RELEASE entities or verified details
        if candidate.entity_type == EntityType.RELEASE or candidate.release_details.catalog_number:
            proposals.extend(self._release_proposals_from_candidate(asset.id, candidate, app_score))
        return proposals

    def _release_proposals_from_candidate(
        self, file_id: str, candidate: CandidateMatch, app_score: float
    ) -> list[FieldProposal]:
        """Extract release-specific FieldProposals (label, catalog_number, vinyl_position)."""
        release = candidate.release_details
        release_fields: list[tuple[str, str | None]] = [
            (FieldName.RELEASE_TITLE.value, release.release_title),
            (FieldName.LABEL.value, release.label),
            (FieldName.CATALOG_NUMBER.value, release.catalog_number),
            (FieldName.VINYL_POSITION.value, release.vinyl_position),
            (FieldName.TRACK_NUMBER.value, release.track_number),
            (FieldName.DISC_NUMBER.value, release.disc_number),
            (FieldName.YEAR.value, release.year),
            (FieldName.GENRE.value, release.genre),
        ]
        return [
            self._make_proposal(file_id, f_name, value, candidate, app_score)
            for f_name, value in release_fields
            if value and provider_may_supply(f_name, candidate.provider)
        ]

    def _make_proposal(
        self,
        file_id: str,
        field_name: str,
        value: str,
        candidate: CandidateMatch,
        app_score: float,
    ) -> FieldProposal:
        """Construct a FieldProposal preserving both provider_confidence and app_score."""
        digest = (
            f"{file_id}:{field_name}:{candidate.provider}:{candidate.provider_entity_id}:{value}"
        )
        # uuid5 hashes with SHA-1. The ID only deduplicates proposal rows. No security check
        # relies on it. Stored rows depend on the same IDs.
        proposal_id = str(uuid.uuid5(uuid.NAMESPACE_OID, digest))
        return FieldProposal(
            id=proposal_id,
            file_id=file_id,
            field_name=field_name,
            proposed_value=value.strip(),
            provider=candidate.provider,
            provider_entity_id=candidate.provider_entity_id,
            evidence=candidate.match_evidence,
            provider_confidence=candidate.match_score,
            application_match_score=app_score,
            review_status=ProposalReviewStatus.PENDING,
        )

    def _analysis_proposal_id(
        self, file_id: str, field_name: str, analysis: AnalysisResult, value: str
    ) -> str:
        """Return the proposal ID for one analysis value.

        The ID covers every column of the proposal table's unique key. A changed value or a
        new analysis row then gets its own ID and never collides with the stored row.
        """
        digest = f"{file_id}:{field_name}:{analysis.analyzer_name}:{analysis.id}:{value}"
        # uuid5 hashes with SHA-1. This deduplication ID carries no security role.
        return str(uuid.uuid5(uuid.NAMESPACE_OID, digest))

    def _proposals_from_analysis(
        self, file_id: str, analysis: AnalysisResult
    ) -> list[FieldProposal]:
        """Create authoritative BPM and Key FieldProposals from local audio analysis (Rule 5)."""
        proposals: list[FieldProposal] = []
        strength = (
            analysis.analysis_strength
            if analysis.analysis_strength is not None
            else DEFAULT_ANALYSIS_CONFIDENCE
        )
        if analysis.bpm is not None:
            bpm_text = f"{analysis.bpm:.1f}"
            proposals.append(
                FieldProposal(
                    id=self._analysis_proposal_id(file_id, FieldName.BPM.value, analysis, bpm_text),
                    file_id=file_id,
                    field_name=FieldName.BPM.value,
                    proposed_value=bpm_text,
                    provider=analysis.analyzer_name,
                    provider_entity_id=analysis.id,
                    evidence={"analyzer_version": analysis.analyzer_version},
                    provider_confidence=strength,
                    application_match_score=strength,
                )
            )
        if analysis.key_tonic is not None and analysis.key_mode is not None:
            standard_key = format_canonical_key(
                analysis.key_tonic, analysis.key_mode, KeyDisplayNotation.STANDARD
            )
            proposals.append(
                FieldProposal(
                    id=self._analysis_proposal_id(
                        file_id, FieldName.KEY.value, analysis, standard_key
                    ),
                    file_id=file_id,
                    field_name=FieldName.KEY.value,
                    proposed_value=standard_key,
                    provider=analysis.analyzer_name,
                    provider_entity_id=analysis.id,
                    evidence={
                        "tonic": str(analysis.key_tonic),
                        "mode": str(analysis.key_mode),
                    },
                    provider_confidence=strength,
                    application_match_score=strength,
                )
            )
        return proposals

    def _check_and_attach_pitch_drift(
        self,
        asset: FileAsset,
        candidate: CandidateMatch,
        analyses: list[AnalysisResult],
    ) -> None:
        """Append Vinyl Pitch Drift warning to AnalysisResult if duration discrepancy matches."""
        bpm = analyses[0].bpm if analyses else None
        warn = detect_vinyl_pitch_drift(
            file_duration_sec=asset.duration,
            catalog_duration_sec=candidate.duration,
            analyzed_bpm=bpm,
        )
        if warn and analyses and warn not in analyses[0].warnings:
            analyses[0].warnings.append(warn)
            self.repo.upsert_analysis_result(analyses[0])

    def _update_review_status_after_reconcile(
        self, file_id: str, proposals: list[FieldProposal]
    ) -> None:
        """Update FileAsset review_status based on proposal presence and conflicts."""
        approved = self.repo.get_approved_fields_map(file_id)
        if approved:
            return
        if not proposals:
            self.repo.update_file_status(file_id, review_status=ReviewStatus.NO_MATCH)
            return
        by_field: dict[str, set[str]] = {}
        for proposal in proposals:
            by_field.setdefault(proposal.field_name, set()).add(proposal.proposed_value.lower())
        has_conflict = any(len(values) > 1 for values in by_field.values())
        status = (
            ReviewStatus.CONFLICTING_CANDIDATES if has_conflict else ReviewStatus.READY_FOR_REVIEW
        )
        self.repo.update_file_status(file_id, review_status=status)
