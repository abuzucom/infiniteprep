"""Providers for a lookup the desktop app has not connected yet.

Each provider reports itself unavailable. The orchestrator skips an unavailable
provider. Each lookup returns no candidates. A direct call never stores invented metadata.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.models import CandidateMatch
from infiniteprep.domain.ports import FingerprintResult, MetadataSearchQuery

NOT_CONNECTED_REASON: Final[str] = "Online identification is not connected in the desktop app."


class UnconfiguredFingerprintProvider:
    """A FingerprintProvider that is never available and identifies nothing."""

    provider_name: str = "fingerprint_unconfigured"
    is_cloud_opt_in: bool = False

    def is_available(self) -> tuple[bool, str | None]:
        """Return (False, reason). The desktop app connects no fingerprint service."""
        return False, NOT_CONNECTED_REASON

    def identify(self, audio_reference: Path, file_id: str | None = None) -> FingerprintResult:
        """Return a result with no fingerprint and no matches."""
        return FingerprintResult(
            provider=self.provider_name,
            raw_fingerprint=None,
            duration=None,
            warnings=[NOT_CONNECTED_REASON],
        )


class UnconfiguredCatalogProvider:
    """A MetadataProvider that is never available and finds nothing."""

    provider_name: str = "catalog_unconfigured"

    def is_available(self) -> tuple[bool, str | None]:
        """Return (False, reason). The desktop app connects no catalog service."""
        return False, NOT_CONNECTED_REASON

    def search(self, query: MetadataSearchQuery, file_id: str = "") -> list[CandidateMatch]:
        """Return no candidates."""
        return []

    def fetch(
        self, entity_id: str, entity_type: EntityType = EntityType.RELEASE, file_id: str = ""
    ) -> list[CandidateMatch]:
        """Return no candidates."""
        return []
