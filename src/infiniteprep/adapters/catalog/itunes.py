"""iTunes Search adapter for a coarse genre and a release-date fallback (D404).

Apple limits promotional content, album art included, to promoting store
content. The adapter therefore reads the genre and the release date and keeps no
artwork from the response.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Final

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.models import CandidateMatch, ReleaseDetails
from infiniteprep.domain.ports import MetadataSearchQuery
from infiniteprep.domain.similarity import string_similarity
from infiniteprep.user_agent import USER_AGENT

ITUNES_SEARCH_URL: Final[str] = "https://itunes.apple.com/search"
ITUNES_USER_AGENT: Final[str] = USER_AGENT
ITUNES_CACHE_TTL_SEC: Final[int] = 86400
# The published limit is about 20 calls per minute. The adapter waits 3 seconds
# between calls so the limit holds for a single client.
ITUNES_MIN_INTERVAL_SEC: Final[float] = 3.0
DEFAULT_COUNTRY: Final[str] = "US"
RESULT_LIMIT: Final[int] = 10
MAX_API_RESPONSE_BYTES: Final[int] = 5_242_880  # 5 MiB cap per API response
# iTunes genres stay coarse, so the provider confidence sits below the value the
# other catalog sources reach.
ITUNES_MATCH_SCORE: Final[float] = 0.60
DISCLOSURE_HOST: Final[str] = "itunes.apple.com"
DISCLOSURE_QUERY_DATA: Final[str] = (
    "the artist name and track title as the search term, the storefront country, "
    "the client IP address, and the shared User-Agent"
)
DISCLOSURE_TEXT: Final[str] = (
    f"Live identification sends {DISCLOSURE_QUERY_DATA} to {DISCLOSURE_HOST}. "
    "The adapter keeps no artwork from the response."
)
RELEASE_YEAR_RE: Final[re.Pattern[str]] = re.compile(r"^(\d{4})")
# A row without a release date sorts after every dated row.
MISSING_RELEASE_DATE: Final[str] = "9999-99-99"


class ItunesSearchAdapter:
    """Queries iTunes Search for a coarse genre and a release date."""

    provider_name: str = "itunes"

    def __init__(
        self,
        repository: Repository,
        http_get_json: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
        min_interval_sec: float = ITUNES_MIN_INTERVAL_SEC,
        country: str = DEFAULT_COUNTRY,
    ) -> None:
        """Initialize ItunesSearchAdapter with Repository cache, rate limiter, and country."""
        self.repo = repository
        self.http_get_json = http_get_json or self._default_http_get_json
        self.min_interval_sec = min_interval_sec
        self.country = country
        self._last_request_ts: float = 0.0

    def is_available(self) -> tuple[bool, str | None]:
        """Return availability status. iTunes Search needs no key."""
        return True, None

    def search(self, query: MetadataSearchQuery, file_id: str = "") -> list[CandidateMatch]:
        """Search iTunes Search for the track named by the query."""
        term = self._search_term(query)
        if not term:
            return []
        params = {
            "term": term,
            "media": "music",
            "entity": "song",
            "country": self.country,
            "limit": str(RESULT_LIMIT),
        }
        cache_key = self._cache_key(params)
        payload = self.repo.get_cached_response(cache_key)
        if payload is None:
            self._respect_rate_limit()
            payload = self.http_get_json(ITUNES_SEARCH_URL, params)
            self.repo.set_cached_response(
                cache_key=cache_key,
                provider=self.provider_name,
                response_data=payload,
                ttl_seconds=ITUNES_CACHE_TTL_SEC,
            )
        results = payload.get("results", [])
        if not isinstance(results, list):
            return []
        row = self._select_row(results, query)
        if row is None:
            return []
        return [self._candidate_from_row(row, query, file_id)]

    def fetch(
        self, entity_id: str, entity_type: EntityType = EntityType.RELEASE, file_id: str = ""
    ) -> list[CandidateMatch]:
        """Return no candidate. iTunes Search has no endpoint that fetches one entity."""
        _ = (entity_id, entity_type, file_id)
        return []

    def _search_term(self, query: MetadataSearchQuery) -> str:
        """Return the search term built from the artist and the title."""
        parts = [part.strip() for part in (query.artist, query.title) if part and part.strip()]
        return " ".join(parts)

    def _cache_key(self, params: dict[str, str]) -> str:
        """Return the cache key for one request."""
        raw = json.dumps(params, sort_keys=True)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return f"itunes:search:{digest}"

    def _respect_rate_limit(self) -> None:
        """Sleep if necessary to hold the published iTunes Search call rate."""
        if self.min_interval_sec <= 0.0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.min_interval_sec:
            time.sleep(self.min_interval_sec - elapsed)
        self._last_request_ts = time.monotonic()

    def _select_row(
        self, results: list[Any], query: MetadataSearchQuery
    ) -> dict[str, Any] | None:
        """Return the best matching row, keeping the earliest date among tied rows."""
        scored = [(self._row_score(row, query), row) for row in results if isinstance(row, dict)]
        if not scored:
            return None
        best_score = max(score for score, _row in scored)
        tied = [row for score, row in scored if score == best_score]
        return min(tied, key=self._row_date_key)

    def _row_score(self, row: dict[str, Any], query: MetadataSearchQuery) -> float:
        """Return the similarity of one row against the artist and the title."""
        artist_score = string_similarity(query.artist, str(row.get("artistName", "")))
        title_score = string_similarity(query.title, str(row.get("trackName", "")))
        return artist_score + title_score

    def _row_date_key(self, row: dict[str, Any]) -> tuple[str, str]:
        """Return the sort key that puts the earliest release date first."""
        release_date = str(row.get("releaseDate", "")).strip()
        if not release_date:
            return (MISSING_RELEASE_DATE, str(row.get("trackId", "")))
        return (release_date, str(row.get("trackId", "")))

    def _candidate_from_row(
        self, row: dict[str, Any], query: MetadataSearchQuery, file_id: str
    ) -> CandidateMatch:
        """Build a release candidate from one iTunes row. No artwork field is copied."""
        track_id = str(row.get("trackId", ""))
        collection_id = str(row.get("collectionId", ""))
        release_date = str(row.get("releaseDate", "")).strip()
        year_match = RELEASE_YEAR_RE.match(release_date)
        duration_ms = row.get("trackTimeMillis")
        duration = float(duration_ms) / 1000.0 if duration_ms else None
        return CandidateMatch(
            id=f"itunes-track-{track_id}",
            file_id=file_id,
            provider=self.provider_name,
            provider_entity_id=track_id,
            entity_type=EntityType.RELEASE,
            artist=str(row.get("artistName", "")) or query.artist,
            title=str(row.get("trackName", "")) or query.title,
            duration=duration,
            release_details=ReleaseDetails(
                release_title=str(row.get("collectionName", "")) or None,
                year=year_match.group(1) if year_match else None,
                genre=str(row.get("primaryGenreName", "")).strip() or None,
            ),
            match_evidence={
                "itunes_track_id": track_id,
                "itunes_collection_id": collection_id,
                "itunes_release_date": release_date,
                "source_url": ITUNES_SEARCH_URL,
            },
            match_score=ITUNES_MATCH_SCORE,
        )

    def _default_http_get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        """Perform HTTPS GET request to the iTunes Search endpoint."""
        if not url.startswith("https://"):
            raise ValueError(f"iTunes Search URL must use https:// scheme: {url}")
        encoded = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{url}?{encoded}",
            headers={"User-Agent": ITUNES_USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            raw_bytes = response.read(MAX_API_RESPONSE_BYTES + 1)
        if len(raw_bytes) > MAX_API_RESPONSE_BYTES:
            raise ValueError("iTunes Search HTTP response exceeded maximum byte size limit.")
        return dict(json.loads(raw_bytes.decode("utf-8")))
