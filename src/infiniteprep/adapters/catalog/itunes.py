"""iTunes Search adapter for a coarse genre and a release-date fallback (D404).

Apple limits promotional content, album art included, to promoting store
content. The adapter therefore reads the genre and the release date and keeps no
artwork from the response.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Final

from infiniteprep.adapters import http_json
from infiniteprep.adapters.http_json import (
    build_candidate_id,
    cached_lookup,
    fetch_json,
)
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.models import CandidateMatch, ReleaseDetails
from infiniteprep.domain.ports import MetadataSearchQuery
from infiniteprep.domain.similarity import string_similarity
from infiniteprep.user_agent import USER_AGENT

ITUNES_SEARCH_URL: Final[str] = "https://itunes.apple.com/search"
ITUNES_SEARCH_HOST: Final[str] = "itunes.apple.com"
ITUNES_USER_AGENT: Final[str] = USER_AGENT
ITUNES_CACHE_TTL_SEC: Final[int] = 86400
# The published limit is about 20 calls per minute. The limiter waits 3 seconds
# between calls so the limit holds for every caller in the process.
ITUNES_MIN_INTERVAL_SEC: Final[float] = 3.0
# A lookup answers in well under a second. The timeout keeps a stalled
# connection from holding a lookup slot for longer.
ITUNES_REQUEST_TIMEOUT_SEC: Final[int] = 15
DEFAULT_COUNTRY: Final[str] = "US"
RESULT_LIMIT: Final[int] = 10
MAX_API_RESPONSE_BYTES: Final[int] = 5_242_880  # 5 MiB cap per API response
# A row supplies a value only when it matches the artist and the title closely
# enough. The mean of the two field scores reaches the match score, and each
# field reaches the field score on its own.
MIN_MATCH_SCORE: Final[float] = 0.60
MIN_FIELD_SCORE: Final[float] = 0.50
# The response fields reach the candidate. The length cap keeps a long value
# out of a proposal.
MAX_FIELD_LENGTH: Final[int] = 500
DISCLOSURE_QUERY_DATA: Final[str] = (
    "the artist name and track title as the search term, the storefront country, "
    "the client IP address, and the shared User-Agent"
)
DISCLOSURE_TEXT: Final[str] = (
    f"The iTunes Search adapter sends {DISCLOSURE_QUERY_DATA} to {ITUNES_SEARCH_HOST}. "
    "The adapter keeps no artwork from the response."
)
# The cached response holds the fields the adapter reads. The artwork fields and
# every other promotional field stay out of the shared cache.
CACHED_RESULT_FIELDS: Final[tuple[str, ...]] = (
    "trackId",
    "collectionId",
    "artistName",
    "collectionName",
    "trackName",
    "primaryGenreName",
    "releaseDate",
    "trackTimeMillis",
)
RELEASE_YEAR_RE: Final[re.Pattern[str]] = re.compile(r"^(\d{4})")
# A row without a release date sorts after every dated row.
MISSING_RELEASE_DATE: Final[str] = "9999-99-99"


class RequestLimiter(http_json.RequestLimiter):
    """Holds the request timing for one provider across every adapter instance.

    The limiter lives in the process, so a second adapter or a second thread
    waits for the same interval. The class keeps the iTunes interval as the
    default.
    """

    def __init__(self, min_interval_sec: float = ITUNES_MIN_INTERVAL_SEC) -> None:
        """Initialize the limiter with the minimum interval between requests."""
        super().__init__(min_interval_sec)


# The one limiter for the provider. Every adapter in the process shares it.
REQUEST_LIMITER: Final[RequestLimiter] = RequestLimiter(ITUNES_MIN_INTERVAL_SEC)


def build_search_opener() -> urllib.request.OpenerDirector:
    """Return an opener that follows no redirect.

    The opener holds the standard handlers without the redirect handler, so a
    redirect response becomes an error and no request ever leaves the published
    endpoint.
    """
    opener = urllib.request.OpenerDirector()
    for handler_class in (
        urllib.request.ProxyHandler,
        urllib.request.HTTPHandler,
        urllib.request.HTTPSHandler,
        urllib.request.HTTPDefaultErrorHandler,
        urllib.request.HTTPErrorProcessor,
        urllib.request.UnknownHandler,
    ):
        opener.add_handler(handler_class())
    return opener


def _text(value: Any, limit: int = MAX_FIELD_LENGTH) -> str:
    """Return a trimmed text value with a length cap."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _identifier(value: Any) -> str:
    """Return a trimmed identifier from a string or a number."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    return _text(value)


def _duration_seconds(value: Any) -> float | None:
    """Return the duration in seconds, or None when the value is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or value <= 0:
        return None
    return float(value) / 1000.0


def _cacheable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the response fields that the shared cache keeps.

    The cached payload holds no artwork and no other promotional field.
    """
    results = payload.get("results")
    if not isinstance(results, list):
        return {"resultCount": 0, "results": []}
    kept_rows = [
        {name: row[name] for name in CACHED_RESULT_FIELDS if name in row}
        for row in results
        if isinstance(row, dict)
    ]
    return {"resultCount": len(kept_rows), "results": kept_rows}


class ItunesSearchAdapter:
    """Queries iTunes Search for a coarse genre and a release date."""

    provider_name: str = "itunes"

    def __init__(
        self,
        repository: Repository,
        http_get_json: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
        *,
        country: str = DEFAULT_COUNTRY,
        limiter: RequestLimiter | None = None,
    ) -> None:
        """Initialize ItunesSearchAdapter with the Repository cache and the country."""
        self.repo = repository
        self.http_get_json = (
            http_get_json if http_get_json is not None else self._default_http_get_json
        )
        self.country = country
        self.limiter = limiter or REQUEST_LIMITER

    def is_available(self) -> tuple[bool, str | None]:
        """Return the availability status. iTunes Search needs no key."""
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
            with self.limiter.request_slot():
                payload = self._fetch_uncached(cache_key, params)
        if payload is None:
            return []
        results = payload.get("results", [])
        if not isinstance(results, list):
            return []
        row = self._select_row(results, query)
        if row is None:
            return []
        score = self._row_score(row, query)
        return [self._candidate_from_row(row, query, file_id, score)]

    def fetch(
        self, entity_id: str, entity_type: EntityType = EntityType.RELEASE, file_id: str = ""
    ) -> list[CandidateMatch]:
        """Return no candidate. iTunes Search has no endpoint that fetches one entity."""
        _ = (entity_id, entity_type, file_id)
        return []

    def _fetch_uncached(self, cache_key: str, params: dict[str, str]) -> dict[str, Any] | None:
        """Return the cached or fetched response, or None after a failed lookup.

        The caller holds the provider slot. Another thread may have cached the
        response while this one waited, so the cache check runs again first. A
        failed lookup fails this file only. The job continues.
        """
        return cached_lookup(
            self.repo,
            cache_key=cache_key,
            provider=self.provider_name,
            label="iTunes Search",
            ttl_seconds=ITUNES_CACHE_TTL_SEC,
            fetch=lambda: self.http_get_json(ITUNES_SEARCH_URL, params),
            cache_form=_cacheable_payload,
        )

    def _search_term(self, query: MetadataSearchQuery) -> str:
        """Return the search term built from the artist and the title."""
        parts = [part.strip() for part in (query.artist, query.title) if part and part.strip()]
        return " ".join(parts)

    def _cache_key(self, params: dict[str, str]) -> str:
        """Return the cache key for one request.

        The key holds the endpoint and the provider, so a response never
        reaches another endpoint or another provider.
        """
        raw = json.dumps(
            {"provider": self.provider_name, "endpoint": ITUNES_SEARCH_URL, "params": params},
            sort_keys=True,
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return f"itunes:search:{digest}"

    def _select_row(self, results: list[Any], query: MetadataSearchQuery) -> dict[str, Any] | None:
        """Return the best matching row that reaches the minimum similarity."""
        eligible: list[tuple[float, dict[str, Any]]] = []
        for row in results:
            if not isinstance(row, dict):
                continue
            artist_score, title_score = self._row_field_scores(row, query)
            if artist_score < MIN_FIELD_SCORE or title_score < MIN_FIELD_SCORE:
                continue
            eligible.append(((artist_score + title_score) / 2.0, row))
        if not eligible:
            return None
        best_score = max(score for score, _row in eligible)
        if best_score < MIN_MATCH_SCORE:
            return None
        tied = [row for score, row in eligible if score == best_score]
        return min(tied, key=self._row_date_key)

    def _row_field_scores(
        self, row: dict[str, Any], query: MetadataSearchQuery
    ) -> tuple[float, float]:
        """Return the artist similarity and the title similarity of one row."""
        artist_score = string_similarity(query.artist, _text(row.get("artistName")))
        title_score = string_similarity(query.title, _text(row.get("trackName")))
        return (artist_score, title_score)

    def _row_score(self, row: dict[str, Any], query: MetadataSearchQuery) -> float:
        """Return the mean similarity of one row against the artist and the title."""
        artist_score, title_score = self._row_field_scores(row, query)
        return (artist_score + title_score) / 2.0

    def _row_date_key(self, row: dict[str, Any]) -> tuple[str, str]:
        """Return the sort key that puts the earliest release date first."""
        release_date = _text(row.get("releaseDate"))
        if not release_date:
            return (MISSING_RELEASE_DATE, _identifier(row.get("trackId")))
        return (release_date, _identifier(row.get("trackId")))

    def _candidate_from_row(
        self,
        row: dict[str, Any],
        query: MetadataSearchQuery,
        file_id: str,
        score: float,
    ) -> CandidateMatch:
        """Build a release candidate from one iTunes row. No artwork field is copied."""
        track_id = _identifier(row.get("trackId"))
        collection_id = _identifier(row.get("collectionId"))
        release_date = _text(row.get("releaseDate"))
        year_match = RELEASE_YEAR_RE.match(release_date)
        return CandidateMatch(
            id=build_candidate_id("itunes-track", file_id, track_id),
            file_id=file_id,
            provider=self.provider_name,
            provider_entity_id=track_id,
            entity_type=EntityType.RELEASE,
            artist=_text(row.get("artistName")) or (query.artist or ""),
            title=_text(row.get("trackName")) or (query.title or ""),
            duration=_duration_seconds(row.get("trackTimeMillis")),
            release_details=ReleaseDetails(
                release_title=_text(row.get("collectionName")) or None,
                year=year_match.group(1) if year_match else None,
                genre=_text(row.get("primaryGenreName")) or None,
            ),
            match_evidence={
                "itunes_track_id": track_id,
                "itunes_collection_id": collection_id,
                "itunes_release_date": release_date,
                "source_url": ITUNES_SEARCH_URL,
            },
            match_score=score,
        )

    def _default_http_get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        """Perform HTTPS GET request to the published iTunes Search endpoint."""
        if not url.startswith(f"https://{ITUNES_SEARCH_HOST}/"):
            raise ValueError(f"iTunes Search URL must use the published endpoint: {url}")
        try:
            return fetch_json(
                url,
                params,
                {"User-Agent": ITUNES_USER_AGENT, "Accept": "application/json"},
                provider="iTunes Search",
                timeout=ITUNES_REQUEST_TIMEOUT_SEC,
                max_bytes=MAX_API_RESPONSE_BYTES,
                open_url=build_search_opener().open,
            )
        except urllib.error.HTTPError as error:
            raise ValueError(f"iTunes Search request failed: {error.code}") from error
