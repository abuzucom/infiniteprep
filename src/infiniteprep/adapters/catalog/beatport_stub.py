"""Gated Beatport catalog adapter stub (requires official partner OAuth2 credentials)."""

from __future__ import annotations

from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.models import CandidateMatch
from infiniteprep.domain.ports import MetadataSearchQuery


class BeatportStubAdapter:
    """Stub adapter for Beatport v4 API; never scrapes HTML and requires partner token."""

    provider_name: str = "beatport"

    def __init__(self, oauth_token: str | None = None) -> None:
        """Initialize BeatportStubAdapter with optional partner OAuth2 token."""
        self.oauth_token = oauth_token

    def is_available(self) -> tuple[bool, str | None]:
        """Return False unless an official Beatport v4 OAuth2 token is configured."""
        if not self.oauth_token:
            return (
                False,
                "Beatport v4 API requires partner OAuth2 credentials "
                "(HTML scraping is prohibited).",
            )
        return True, None

    def search(self, query: MetadataSearchQuery, file_id: str = "") -> list[CandidateMatch]:
        """Return empty list when Beatport partner credentials are not present."""
        _ = (query, file_id)
        return []

    def fetch(
        self, entity_id: str, entity_type: EntityType = EntityType.RELEASE, file_id: str = ""
    ) -> list[CandidateMatch]:
        """Return empty list when Beatport partner credentials are not present."""
        _ = (entity_id, entity_type, file_id)
        return []
