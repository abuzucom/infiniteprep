"""Gated Beatport catalog adapter stub (requires official partner OAuth2 credentials)."""

from __future__ import annotations

from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.models import CandidateMatch
from infiniteprep.domain.ports import MetadataSearchQuery

BEATPORT_NOT_IMPLEMENTED = (
    "The Beatport adapter sends no request yet; Beatport results are unavailable."
)


class BeatportStubAdapter:
    """Stub adapter for Beatport v4 API; never scrapes HTML and requires partner token."""

    provider_name: str = "beatport"

    def __init__(self, oauth_token: str | None = None) -> None:
        """Initialize BeatportStubAdapter with optional partner OAuth2 token."""
        self.oauth_token = oauth_token

    def is_available(self) -> tuple[bool, str | None]:
        """Return False. The adapter sends no Beatport request yet.

        A token alone made the stub report available while every lookup returned
        nothing, so a caller could not tell a missing match from a missing adapter.
        """
        if not self.oauth_token:
            return (
                False,
                "Beatport v4 API requires partner OAuth2 credentials "
                "(HTML scraping is prohibited).",
            )
        return False, BEATPORT_NOT_IMPLEMENTED

    def search(self, query: MetadataSearchQuery, file_id: str = "") -> list[CandidateMatch]:
        """Return an empty list. The adapter sends no Beatport request yet."""
        _ = (query, file_id)
        return []

    def fetch(
        self, entity_id: str, entity_type: EntityType = EntityType.RELEASE, file_id: str = ""
    ) -> list[CandidateMatch]:
        """Return an empty list. The adapter sends no Beatport request yet."""
        _ = (entity_id, entity_type, file_id)
        return []
