"""Mock catalog provider adapter for deterministic offline release and recording lookups."""

from __future__ import annotations

from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.models import CandidateMatch, ReleaseDetails
from infiniteprep.domain.ports import MetadataSearchQuery


class MockCatalogProviderAdapter:
    """Offline mock MetadataProvider returning deterministic 1990s-2010s dance candidates."""

    provider_name: str = "discogs_mock"

    def is_available(self) -> tuple[bool, str | None]:
        """Return availability status for mock catalog provider."""
        return True, None

    def search(self, query: MetadataSearchQuery, file_id: str = "") -> list[CandidateMatch]:
        """Search mock catalog entries matching query fields."""
        artist = query.artist or "Deep Dish"
        title = query.title or "The Dream"
        cat_no = query.catalog_number or "TRIUK014"
        rel_title = query.release_title or f"{title} (12-Inch Single)"
        return [
            CandidateMatch(
                id=f"mock-cat-{file_id or 'default'}-1",
                file_id=file_id,
                provider=self.provider_name,
                provider_entity_id=f"rel-{cat_no.lower()}",
                entity_type=EntityType.RELEASE,
                artist=artist,
                title=title,
                mix_name=query.mix_name or "Sharam's Deep Dish Dreamscape Mix",
                remixer=query.remixer or "Sharam",
                duration=query.duration or 480.0,
                release_details=ReleaseDetails(
                    release_title=rel_title,
                    label=query.label or "Tribal America",
                    catalog_number=cat_no,
                    country="UK",
                    format_description='Vinyl, 12", 33 1/3 RPM',
                    vinyl_position="A1",
                    year="1994",
                    track_number="A1",
                    disc_number="1",
                    genre="Progressive House",
                ),
                match_evidence={"source": "mock_catalog", "catalog_number": cat_no},
                match_score=0.93,
            )
        ]

    def search_candidates(
        self,
        *,
        file_id: str,
        artist: str | None = None,
        title: str | None = None,
        release_title: str | None = None,
        catalog_number: str | None = None,
        duration_sec: float | None = None,
    ) -> list[CandidateMatch]:
        """Convenience wrapper around search() taking keyword arguments."""
        query = MetadataSearchQuery(
            artist=artist,
            title=title,
            release_title=release_title,
            catalog_number=catalog_number,
            duration=duration_sec,
        )
        return self.search(query, file_id=file_id)

    def fetch(
        self, entity_id: str, entity_type: EntityType = EntityType.RELEASE, file_id: str = ""
    ) -> list[CandidateMatch]:
        """Fetch a specific mock candidate by entity_id."""
        query = MetadataSearchQuery(release_id=entity_id, catalog_number="TRIUK014")
        results = self.search(query, file_id=file_id)
        for item in results:
            item.provider_entity_id = entity_id
            item.entity_type = entity_type
        return results
