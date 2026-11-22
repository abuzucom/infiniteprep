"""Behavioral tests for the per-field source priority filter (D394, D404)."""

from __future__ import annotations

import pytest

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import ApprovalOrigin, AudioFormat, EntityType, ProposalReviewStatus
from infiniteprep.domain.models import (
    ApprovedField,
    CandidateMatch,
    FieldProposal,
    FileAsset,
    ReleaseDetails,
)
from infiniteprep.domain.source_priority import FIELD_PRIORITY, outranks, priority_rank
from infiniteprep.services.reconciliation import ReconciliationService
from tests.fixtures.memory_repository import build_memory_repository

FILE_ID = "file-1"


def _asset() -> FileAsset:
    """Return the library row the proposals attach to."""
    return FileAsset(
        id=FILE_ID,
        current_path="/music/file-1.mp3",
        original_path="/music/file-1.mp3",
        file_size=1024,
        content_hash="a" * 64,
        format=AudioFormat.MP3,
        duration=480.0,
        original_tags={"standard_tags": {"artist": "Deep Dish", "title": "The Dream"}},
        import_batch_id="batch-1",
        source_group_id=None,
    )


def _candidate(provider: str, entity_id: str, **release_details: str) -> CandidateMatch:
    """Return a release candidate for one provider."""
    return CandidateMatch(
        id=f"{provider}-rel-{entity_id}",
        file_id=FILE_ID,
        provider=provider,
        provider_entity_id=entity_id,
        entity_type=EntityType.RELEASE,
        artist="Deep Dish",
        title="The Dream",
        release_details=ReleaseDetails(**release_details),
        match_evidence={},
        match_score=0.9,
    )


def _reconcile(repository: Repository) -> list:
    """Return the proposals after a reconciliation run."""
    return ReconciliationService(repository).reconcile_file(FILE_ID)


def test_priority_table_orders_sources() -> None:
    """The genre and the release date carry their own order of providers."""
    assert FIELD_PRIORITY["genre"] == ("discogs", "itunes")
    assert FIELD_PRIORITY["year"] == ("musicbrainz", "discogs", "itunes")
    assert priority_rank("genre", "discogs") == 0
    assert priority_rank("genre", "itunes") == 1
    assert priority_rank("label", "itunes") is None
    assert outranks("genre", "discogs", "itunes") is True
    assert outranks("genre", "itunes", "discogs") is False


def test_higher_rank_suppresses_lower() -> None:
    """The Discogs genre and year keep the field and the iTunes values never appear."""
    repository = build_memory_repository()
    repository.upsert_file_asset(_asset())
    repository.upsert_candidate_match(_candidate("discogs", "1", genre="Electronic", year="1995"))
    repository.upsert_candidate_match(_candidate("itunes", "2", genre="Dance", year="1994"))

    proposals = _reconcile(repository)

    genre_proposals = [item for item in proposals if item.field_name == "genre"]
    year_proposals = [item for item in proposals if item.field_name == "year"]
    assert [item.provider for item in genre_proposals] == ["discogs"]
    assert [item.provider for item in year_proposals] == ["discogs"]


def test_itunes_stays_without_higher_source() -> None:
    """Without another source the iTunes value reaches the proposal table."""
    repository = build_memory_repository()
    repository.upsert_file_asset(_asset())
    repository.upsert_candidate_match(_candidate("itunes", "2", genre="Dance", year="1994"))

    proposals = _reconcile(repository)

    assert [item.provider for item in proposals if item.field_name == "genre"] == ["itunes"]
    assert [item.provider for item in proposals if item.field_name == "year"] == ["itunes"]


def test_user_value_suppresses_all_proposals() -> None:
    """A value the owner set outranks every provider for that field."""
    repository = build_memory_repository()
    repository.upsert_file_asset(_asset())
    repository.upsert_approved_field(
        ApprovedField(
            file_id=FILE_ID,
            field_name="genre",
            approved_value="Progressive House",
            approval_origin=ApprovalOrigin.MANUAL_EDIT,
            approved_at="2026-11-15T00:00:00+00:00",
            locked=True,
            provider_attribution={"provider": "user_manual"},
        )
    )
    repository.upsert_candidate_match(_candidate("itunes", "2", genre="Dance"))

    proposals = _reconcile(repository)

    assert [item for item in proposals if item.field_name == "genre"] == []


def test_approved_higher_suppresses_lower() -> None:
    """An accepted Discogs genre keeps the field and the iTunes genre never appears."""
    repository = build_memory_repository()
    repository.upsert_file_asset(_asset())
    repository.upsert_approved_field(
        ApprovedField(
            file_id=FILE_ID,
            field_name="genre",
            approved_value="Electronic",
            approval_origin=ApprovalOrigin.PROPOSAL_ACCEPTED,
            approved_at="2026-11-15T00:00:00+00:00",
            locked=False,
            provider_attribution={"provider": "discogs", "entity_id": "1"},
        )
    )
    repository.upsert_candidate_match(_candidate("itunes", "2", genre="Dance"))

    proposals = _reconcile(repository)

    assert [item for item in proposals if item.field_name == "genre"] == []


def test_superseded_proposal_is_stored() -> None:
    """A stored iTunes proposal turns superseded when a higher ranked source arrives."""
    repository = build_memory_repository()
    repository.upsert_file_asset(_asset())
    repository.upsert_candidate_match(_candidate("itunes", "2", genre="Dance"))
    _reconcile(repository)

    repository.upsert_candidate_match(_candidate("discogs", "1", genre="Electronic"))
    _reconcile(repository)

    stored = repository.list_field_proposals(FILE_ID)
    itunes_proposals = [item for item in stored if item.provider == "itunes"]
    assert itunes_proposals
    assert all(item.review_status == ProposalReviewStatus.SUPERSEDED for item in itunes_proposals)


def test_vanished_source_turns_superseded() -> None:
    """A stored iTunes proposal turns superseded when a Discogs candidate holds the field."""
    repository = build_memory_repository()
    repository.upsert_file_asset(_asset())
    repository.upsert_field_proposal(
        FieldProposal(
            id="stored-itunes-genre",
            file_id=FILE_ID,
            field_name="genre",
            proposed_value="Dance",
            provider="itunes",
            provider_entity_id="2",
            provider_confidence=0.9,
        )
    )
    repository.upsert_candidate_match(_candidate("discogs", "1", genre="Electronic"))

    _reconcile(repository)

    stored = repository.get_field_proposal_by_id("stored-itunes-genre")
    assert stored is not None
    assert stored.review_status == ProposalReviewStatus.SUPERSEDED


def test_approved_provider_supersedes_lower() -> None:
    """An approved Discogs genre turns a stored iTunes proposal superseded."""
    repository = build_memory_repository()
    repository.upsert_file_asset(_asset())
    repository.upsert_field_proposal(
        FieldProposal(
            id="stored-itunes-genre",
            file_id=FILE_ID,
            field_name="genre",
            proposed_value="Dance",
            provider="itunes",
            provider_entity_id="2",
            provider_confidence=0.9,
        )
    )
    repository.upsert_approved_field(
        ApprovedField(
            file_id=FILE_ID,
            field_name="genre",
            approved_value="Electronic",
            approval_origin=ApprovalOrigin.PROPOSAL_ACCEPTED,
            approved_at="2026-11-15T00:00:00+00:00",
            locked=False,
            provider_attribution={"provider": "discogs", "entity_id": "1"},
        )
    )

    _reconcile(repository)

    stored = repository.get_field_proposal_by_id("stored-itunes-genre")
    assert stored is not None
    assert stored.review_status == ProposalReviewStatus.SUPERSEDED


def test_user_value_supersedes_stored() -> None:
    """A value the owner set turns every stored proposal for the field superseded."""
    repository = build_memory_repository()
    repository.upsert_file_asset(_asset())
    repository.upsert_field_proposal(
        FieldProposal(
            id="stored-itunes-genre",
            file_id=FILE_ID,
            field_name="genre",
            proposed_value="Dance",
            provider="itunes",
            provider_entity_id="2",
            provider_confidence=0.9,
        )
    )
    repository.upsert_approved_field(
        ApprovedField(
            file_id=FILE_ID,
            field_name="genre",
            approved_value="Progressive House",
            approval_origin=ApprovalOrigin.MANUAL_EDIT,
            approved_at="2026-11-15T00:00:00+00:00",
            locked=True,
            provider_attribution={"provider": "user_manual"},
        )
    )

    _reconcile(repository)

    stored = repository.get_field_proposal_by_id("stored-itunes-genre")
    assert stored is not None
    assert stored.review_status == ProposalReviewStatus.SUPERSEDED


def test_unordered_field_keeps_all_proposals() -> None:
    """A field outside the priority table keeps the current behavior."""
    repository = build_memory_repository()
    repository.upsert_file_asset(_asset())
    repository.upsert_candidate_match(_candidate("discogs", "1", label="Tribal America"))
    repository.upsert_candidate_match(_candidate("musicbrainz", "2", label="Other Label"))

    proposals = _reconcile(repository)

    label_proposals = [item for item in proposals if item.field_name == "label"]
    assert {item.provider for item in label_proposals} == {"discogs", "musicbrainz"}


@pytest.mark.parametrize("field_name", ["genre", "year"])
def test_the_lowest_ranked_source_loses_the_field(field_name: str) -> None:
    """Each ranked field names one winner and drops the lower ranked sources."""
    order = FIELD_PRIORITY[field_name]
    repository = build_memory_repository()
    repository.upsert_file_asset(_asset())
    for index, provider in enumerate(order):
        repository.upsert_candidate_match(
            _candidate(provider, str(index), **{field_name: str(1990 + index)})
        )

    proposals = _reconcile(repository)

    field_proposals = [item for item in proposals if item.field_name == field_name]
    assert [item.provider for item in field_proposals] == [order[0]]
