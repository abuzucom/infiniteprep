"""Resolve the metadata shown in the library table."""

from collections.abc import Mapping

from infiniteprep.domain.models import ApprovedField


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
