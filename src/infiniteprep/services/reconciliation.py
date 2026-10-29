"""DJ-aware reconciliation service implementing Rules 1-10 and Vinyl Pitch Drift detection."""

from __future__ import annotations

import uuid
from typing import Any

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    EntityType,
    FieldName,
    KeyDisplayNotation,
    ProposalReviewStatus,
    ReviewStatus,
    format_canonical_key,
)
from infiniteprep.domain.models import AnalysisResult, CandidateMatch, FieldProposal, FileAsset
from infiniteprep.services.matching_engine import MatchingEngine, parse_dj_title_and_mix


def detect_vinyl_pitch_drift(
    *,
    file_duration_sec: float | None,
    catalog_duration_sec: float | None,
    analyzed_bpm: float | None = None,
) -> str | None:
    """Detect turntable pitch offset (2% to 8%) or 33.3 vs 45 RPM speed mismatch."""
    if (
        file_duration_sec is None
        or catalog_duration_sec is None
        or file_duration_sec <= 10.0
        or catalog_duration_sec <= 10.0
    ):
        return None
    speed_ratio = catalog_duration_sec / file_duration_sec
    pct_shift = (speed_ratio - 1.0) * 100.0
    if 1.8 <= abs(pct_shift) <= 8.5:
        bpm_note = ""
        if analyzed_bpm and speed_ratio > 0.0:
            zero_pitch_bpm = round(analyzed_bpm / speed_ratio, 1)
            bpm_note = f"; estimated 0% pitch BPM ~ {zero_pitch_bpm}"
        return (
            f"Vinyl Pitch Drift Warning: File duration ({file_duration_sec:.1f}s) vs catalog "
            f"({catalog_duration_sec:.1f}s) suggests turntable pitch shift of "
            f"{pct_shift:+.1f}%{bpm_note}."
        )
    if abs(speed_ratio - (45.0 / 33.333)) <= 0.06 or abs(speed_ratio - (33.333 / 45.0)) <= 0.06:
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

        for cand in candidates:
            app_score = self.matching.compute_application_match_score(asset, cand)
            generated.extend(self._proposals_from_candidate(asset, cand, app_score))
            self._check_and_attach_pitch_drift(asset, cand, analyses)

        for ana in analyses:
            generated.extend(self._proposals_from_analysis(asset.id, ana))

        for prop in generated:
            self.repo.upsert_field_proposal(prop)

        self._update_review_status_after_reconcile(asset.id, generated)
        return self.repo.list_field_proposals(asset.id)

    def _proposals_from_candidate(
        self, asset: FileAsset, cand: CandidateMatch, app_score: float
    ) -> list[FieldProposal]:
        """Extract recording-level and release-level FieldProposals from a CandidateMatch."""
        props: list[FieldProposal] = []
        base_title, parsed_mix, parsed_remixer = parse_dj_title_and_mix(cand.title or "")
        effective_title = base_title or cand.title
        effective_mix = cand.mix_name or parsed_mix
        effective_remixer = cand.remixer or parsed_remixer

        rec_fields: list[tuple[str, str | None]] = [
            (FieldName.ARTIST.value, cand.artist),
            (FieldName.TITLE.value, effective_title),
            (FieldName.MIX_NAME.value, effective_mix),
            (FieldName.REMIXER.value, effective_remixer),
        ]
        for f_name, val in rec_fields:
            if val:
                props.append(self._make_proposal(asset.id, f_name, val, cand, app_score))

        # Rule 1 & 2: Release-specific fields come only from RELEASE entities or verified details
        if cand.entity_type == EntityType.RELEASE or cand.release_details.catalog_number:
            props.extend(self._release_proposals_from_candidate(asset.id, cand, app_score))
        return props

    def _release_proposals_from_candidate(
        self, file_id: str, cand: CandidateMatch, app_score: float
    ) -> list[FieldProposal]:
        """Extract release-specific FieldProposals (label, catalog_number, vinyl_position)."""
        rel = cand.release_details
        rel_fields: list[tuple[str, str | None]] = [
            (FieldName.RELEASE_TITLE.value, rel.release_title),
            (FieldName.LABEL.value, rel.label),
            (FieldName.CATALOG_NUMBER.value, rel.catalog_number),
            (FieldName.VINYL_POSITION.value, rel.vinyl_position),
            (FieldName.TRACK_NUMBER.value, rel.track_number),
            (FieldName.DISC_NUMBER.value, rel.disc_number),
            (FieldName.YEAR.value, rel.year),
            (FieldName.GENRE.value, rel.genre),
        ]
        return [
            self._make_proposal(file_id, f_name, val, cand, app_score)
            for f_name, val in rel_fields
            if val
        ]

    def _make_proposal(
        self,
        file_id: str,
        field_name: str,
        value: str,
        cand: CandidateMatch,
        app_score: float,
    ) -> FieldProposal:
        """Construct a FieldProposal preserving both provider_confidence and app_score."""
        digest = f"{file_id}:{field_name}:{cand.provider}:{cand.provider_entity_id}:{value}"
        prop_id = str(uuid.uuid5(uuid.NAMESPACE_OID, digest))
        return FieldProposal(
            id=prop_id,
            file_id=file_id,
            field_name=field_name,
            proposed_value=value.strip(),
            provider=cand.provider,
            provider_entity_id=cand.provider_entity_id,
            evidence=cand.match_evidence,
            provider_confidence=cand.match_score,
            application_match_score=app_score,
            review_status=ProposalReviewStatus.PENDING,
        )

    def _proposals_from_analysis(self, file_id: str, ana: AnalysisResult) -> list[FieldProposal]:
        """Create authoritative BPM and Key FieldProposals from local audio analysis (Rule 5)."""
        props: list[FieldProposal] = []
        strength = ana.analysis_strength if ana.analysis_strength is not None else 0.85
        if ana.bpm is not None:
            bpm_str = f"{ana.bpm:.1f}"
            props.append(
                FieldProposal(
                    id=str(uuid.uuid5(uuid.NAMESPACE_OID, f"{file_id}:bpm:{ana.analyzer_name}")),
                    file_id=file_id,
                    field_name=FieldName.BPM.value,
                    proposed_value=bpm_str,
                    provider=ana.analyzer_name,
                    provider_entity_id=ana.id,
                    evidence={"analyzer_version": ana.analyzer_version},
                    provider_confidence=strength,
                    application_match_score=strength,
                )
            )
        if ana.key_tonic is not None and ana.key_mode is not None:
            key_std = format_canonical_key(ana.key_tonic, ana.key_mode, KeyDisplayNotation.STANDARD)
            props.append(
                FieldProposal(
                    id=str(uuid.uuid5(uuid.NAMESPACE_OID, f"{file_id}:key:{ana.analyzer_name}")),
                    file_id=file_id,
                    field_name=FieldName.KEY.value,
                    proposed_value=key_std,
                    provider=ana.analyzer_name,
                    provider_entity_id=ana.id,
                    evidence={
                        "tonic": str(ana.key_tonic),
                        "mode": str(ana.key_mode),
                    },
                    provider_confidence=strength,
                    application_match_score=strength,
                )
            )
        return props

    def _check_and_attach_pitch_drift(
        self,
        asset: FileAsset,
        cand: CandidateMatch,
        analyses: list[AnalysisResult],
    ) -> None:
        """Append Vinyl Pitch Drift warning to AnalysisResult if duration discrepancy matches."""
        bpm = analyses[0].bpm if analyses else None
        warn = detect_vinyl_pitch_drift(
            file_duration_sec=asset.duration,
            catalog_duration_sec=cand.duration,
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
        for p in proposals:
            by_field.setdefault(p.field_name, set()).add(p.proposed_value.lower())
        has_conflict = any(len(vals) > 1 for vals in by_field.values())
        status = (
            ReviewStatus.CONFLICTING_CANDIDATES if has_conflict else ReviewStatus.READY_FOR_REVIEW
        )
        self.repo.update_file_status(file_id, review_status=status)
