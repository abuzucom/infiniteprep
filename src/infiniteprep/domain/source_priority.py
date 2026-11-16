"""Per-field source ranks that decide which provider supplies a value (D394, D404)."""

from __future__ import annotations

from typing import Final

from infiniteprep.domain.enums import ApprovalOrigin, FieldName

GENRE_FIELD: Final[str] = FieldName.GENRE.value
YEAR_FIELD: Final[str] = FieldName.YEAR.value

# The order runs from the highest ranked source to the lowest. Only the fields
# in this table carry an order. Every other field keeps the current behavior.
FIELD_PRIORITY: Final[dict[str, tuple[str, ...]]] = {
    GENRE_FIELD: ("discogs", "itunes"),
    YEAR_FIELD: ("musicbrainz", "discogs", "itunes"),
}

# A value the owner set outranks every provider for every field.
USER_VALUE_ORIGINS: Final[frozenset[ApprovalOrigin]] = frozenset(
    {ApprovalOrigin.USER_EDITED, ApprovalOrigin.MANUAL_EDIT}
)

# A provider that supplies a fallback for some fields never proposes the others.
PROVIDER_FIELD_SCOPE: Final[dict[str, frozenset[str]]] = {
    "itunes": frozenset({GENRE_FIELD, YEAR_FIELD}),
}


def provider_may_supply(field_name: str, provider: str) -> bool:
    """Return whether a provider may supply a field at all."""
    scope = PROVIDER_FIELD_SCOPE.get(provider)
    if scope is None:
        return True
    return field_name in scope


def priority_rank(field_name: str, provider: str) -> int | None:
    """Return the rank of a provider for a field, or None when the field carries no order."""
    order = FIELD_PRIORITY.get(field_name)
    if order is None:
        return None
    try:
        return order.index(provider)
    except ValueError:
        return None


def outranks(field_name: str, provider: str, other_provider: str) -> bool:
    """Return whether a provider ranks ahead of another provider for a field."""
    own_rank = priority_rank(field_name, provider)
    other_rank = priority_rank(field_name, other_provider)
    if own_rank is None or other_rank is None:
        return False
    return own_rank < other_rank
