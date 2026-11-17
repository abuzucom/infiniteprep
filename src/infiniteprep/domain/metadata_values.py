"""Resolve the metadata shown in the library table."""

from collections.abc import Iterable, Mapping
from typing import Final

from infiniteprep.domain.enums import ProposalReviewStatus
from infiniteprep.domain.models import ApprovedField, FieldProposal

# A rejected or superseded proposal lost its field and supplies no effective value.
INACTIVE_PROPOSAL_STATUSES: Final[frozenset[ProposalReviewStatus]] = frozenset(
    {ProposalReviewStatus.REJECTED, ProposalReviewStatus.SUPERSEDED}
)


def top_proposal_values(proposals: Iterable[FieldProposal]) -> dict[str, str]:
    """Return the first active proposed value of each field.

    The caller passes the proposals in rank order, best first. Rejected and superseded
    proposals are skipped.
    """
    values: dict[str, str] = {}
    for proposal in proposals:
        if proposal.review_status in INACTIVE_PROPOSAL_STATUSES:
            continue
        values.setdefault(proposal.field_name, proposal.proposed_value)
    return values


def resolve_effective_value(
    tags: Mapping[str, str],
    approved: Mapping[str, ApprovedField],
    proposals: Mapping[str, str],
    field_name: str,
) -> str:
    """Return the approved value, top proposal, or original tag in that order."""
    if field_name in approved:
        return approved[field_name].approved_value
    return proposals.get(field_name) or tags.get(field_name, "")
