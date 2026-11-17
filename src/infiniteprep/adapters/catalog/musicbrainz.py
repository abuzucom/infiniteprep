"""MusicBrainz ws/2 catalog metadata adapter with 1 req/sec rate limiting and SQLite cache."""

from __future__ import annotations

import hashlib
import re
import urllib.request
from collections.abc import Callable
from typing import Any, Final

from infiniteprep.adapters.http_json import (
    LOOKUP_ERRORS,
    RequestLimiter,
    build_candidate_id,
    fetch_json,
    log_lookup_failure,
)
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.models import CandidateMatch, ReleaseDetails
from infiniteprep.domain.ports import MetadataSearchQuery
from infiniteprep.user_agent import USER_AGENT

MB_BASE_URL: Final[str] = "https://musicbrainz.org/ws/2"
MAX_API_RESPONSE_BYTES: Final[int] = 5_242_880  # 5 MiB cap per API response
MB_USER_AGENT: Final[str] = USER_AGENT
MB_CACHE_TTL_SEC: Final[int] = 86400
LUCENE_SPECIAL_RE: Final[re.Pattern[str]] = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')
SAFE_ENTITY_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# A MusicBrainz release date reads YYYY, YYYY-MM, or YYYY-MM-DD. The class names ASCII
# digits, because \d also matches other Unicode decimal digits.
RELEASE_YEAR_RE: Final[re.Pattern[str]] = re.compile(r"([0-9]{4})(?:-|\Z)")
MB_REQUEST_TIMEOUT_SEC: Final[float] = 15.0
FETCH_ENDPOINTS: Final[dict[EntityType, str]] = {
    EntityType.RECORDING: "recording",
    EntityType.RELEASE: "release",
}


def extract_release_year(raw_date: object) -> str | None:
    """Return the four-digit year a release date starts with, or None for any other shape."""
    if not isinstance(raw_date, str):
        return None
    year_match = RELEASE_YEAR_RE.match(raw_date.strip())
    if year_match is None:
        return None
    return year_match.group(1)


def escape_lucene_term(raw_value: str) -> str:
    """Escape Lucene special characters before wrapping a search value in quotes."""
    cleaned = raw_value.replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
    return LUCENE_SPECIAL_RE.sub(r"\\\1", cleaned)


class MusicBrainzAdapter:
    """Queries MusicBrainz ws/2 API with User-Agent compliance, rate limiting, and caching."""

    provider_name: str = "musicbrainz"

    def __init__(
        self,
        repository: Repository,
        http_get_json: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
        min_interval_sec: float = 1.0,
    ) -> None:
        """Initialize MusicBrainzAdapter with Repository cache and rate limiter."""
        self.repo = repository
        self.http_get_json = http_get_json or self._default_http_get_json
        self.min_interval_sec = min_interval_sec
        self._limiter = RequestLimiter(min_interval_sec)

    def is_available(self) -> tuple[bool, str | None]:
        """Return availability status for MusicBrainz public API."""
        return True, None

    def search(self, query: MetadataSearchQuery, file_id: str = "") -> list[CandidateMatch]:
        """Search MusicBrainz recordings and releases matching MetadataSearchQuery."""
        lucene_parts: list[str] = []
        if query.artist:
            lucene_parts.append(f'artist:"{escape_lucene_term(query.artist)}"')
        if query.title:
            lucene_parts.append(f'recording:"{escape_lucene_term(query.title)}"')
        if query.release_title:
            lucene_parts.append(f'release:"{escape_lucene_term(query.release_title)}"')
        if query.catalog_number:
            lucene_parts.append(f'catno:"{escape_lucene_term(query.catalog_number)}"')
        lucene_query = " AND ".join(lucene_parts) or "recording:*"

        cache_hash = hashlib.sha256(lucene_query.encode("utf-8")).hexdigest()[:24]
        cache_key = f"mb:search:{cache_hash}"
        payload = self._cached_get(
            cache_key,
            f"{MB_BASE_URL}/recording",
            {"query": lucene_query, "fmt": "json", "limit": "10"},
        )
        if payload is None:
            return []
        return self._parse_recording_list(payload.get("recordings", []), file_id=file_id)

    def fetch(
        self, entity_id: str, entity_type: EntityType = EntityType.RELEASE, file_id: str = ""
    ) -> list[CandidateMatch]:
        """Fetch a specific MusicBrainz recording or release by MBID.

        An entity type without a lookup endpoint raises ValueError before any request.
        """
        if not SAFE_ENTITY_ID_RE.match(entity_id):
            raise ValueError(f"Invalid MusicBrainz entity_id '{entity_id}'.")
        endpoint = FETCH_ENDPOINTS.get(entity_type)
        if endpoint is None:
            raise ValueError(f"MusicBrainz fetch does not support entity type '{entity_type}'.")
        payload = self._cached_get(
            f"mb:fetch:{endpoint}:{entity_id}",
            f"{MB_BASE_URL}/{endpoint}/{entity_id}",
            {"inc": "artists+releases+labels+media", "fmt": "json"},
        )
        if payload is None:
            return []
        if entity_type == EntityType.RECORDING:
            return self._parse_recording_list([payload], file_id=file_id)
        return [self._candidate_from_release_lookup(payload, file_id)]

    def _cached_get(
        self, cache_key: str, url: str, params: dict[str, str]
    ) -> dict[str, Any] | None:
        """Return the cached or fetched payload, or None after a failed lookup.

        A network failure or a malformed response fails this lookup only. The
        identification job continues with the other providers.
        """
        payload = self.repo.get_cached_response(cache_key)
        if payload is not None:
            return payload
        self._respect_rate_limit()
        try:
            payload = self.http_get_json(url, params)
        except LOOKUP_ERRORS as error:
            log_lookup_failure("MusicBrainz", error)
            return None
        self.repo.set_cached_response(
            cache_key=cache_key,
            provider=self.provider_name,
            response_data=payload,
            ttl_seconds=MB_CACHE_TTL_SEC,
        )
        return payload

    def _respect_rate_limit(self) -> None:
        """Sleep if necessary to maintain the 1 request/second MusicBrainz limit."""
        self._limiter.wait()

    def _parse_recording_list(
        self, recordings: list[dict[str, Any]], file_id: str
    ) -> list[CandidateMatch]:
        """Convert MusicBrainz recording dicts into CandidateMatch domain entities."""
        results: list[CandidateMatch] = []
        for rec in recordings:
            results.extend(self._candidates_from_recording(rec, file_id))
        return results

    def _candidates_from_recording(self, rec: dict[str, Any], file_id: str) -> list[CandidateMatch]:
        """Extract CandidateMatch objects from a single MusicBrainz recording entry."""
        rec_id = str(rec.get("id", "unknown"))
        title = str(rec.get("title", ""))
        artists = [a.get("name", "") for a in rec.get("artist-credit", []) if isinstance(a, dict)]
        artist_str = ", ".join(a for a in artists if a) or None
        dur_ms = rec.get("length")
        duration = float(dur_ms) / 1000.0 if dur_ms else None
        releases = rec.get("releases", [])
        if not releases:
            return [
                CandidateMatch(
                    id=build_candidate_id("mb-rec", file_id, rec_id),
                    file_id=file_id,
                    provider=self.provider_name,
                    provider_entity_id=rec_id,
                    entity_type=EntityType.RECORDING,
                    artist=artist_str,
                    title=title,
                    duration=duration,
                    match_evidence={"recording_id": rec_id},
                    match_score=0.85,
                )
            ]
        return [
            self._build_release_candidate(
                rec_id=rec_id,
                artist_str=artist_str,
                title=title,
                duration=duration,
                rel=rel,
                file_id=file_id,
            )
            for rel in releases
        ]

    def _build_release_candidate(
        self,
        *,
        rec_id: str,
        artist_str: str | None,
        title: str,
        duration: float | None,
        rel: dict[str, Any],
        file_id: str,
    ) -> CandidateMatch:
        """Build a Release CandidateMatch from a MusicBrainz release sub-object."""
        rel_id = str(rel.get("id", rec_id))
        media = (rel.get("media") or [{}])[0]
        tracks = media.get("track") or [{}]
        track_num = str(tracks[0].get("number") or "") or None
        return CandidateMatch(
            id=build_candidate_id("mb-rel", file_id, rec_id, rel_id),
            file_id=file_id,
            provider=self.provider_name,
            provider_entity_id=rel_id,
            entity_type=EntityType.RELEASE,
            artist=artist_str,
            title=title,
            duration=duration,
            release_details=self._release_details(rel, track_num),
            match_evidence={"recording_id": rec_id, "release_id": rel_id},
            match_score=0.88,
        )

    def _candidate_from_release_lookup(
        self, release: dict[str, Any], file_id: str
    ) -> CandidateMatch:
        """Build a Release CandidateMatch from a MusicBrainz release lookup payload.

        A release lookup names no single track, so the candidate holds no track number.
        """
        rel_id = str(release.get("id", ""))
        credits = release.get("artist-credit") or []
        artists = [a.get("name", "") for a in credits if isinstance(a, dict)]
        return CandidateMatch(
            id=build_candidate_id("mb-rel", file_id, rel_id),
            file_id=file_id,
            provider=self.provider_name,
            provider_entity_id=rel_id,
            entity_type=EntityType.RELEASE,
            artist=", ".join(a for a in artists if a) or None,
            title=str(release.get("title", "")),
            duration=None,
            release_details=self._release_details(release, None),
            match_evidence={"release_id": rel_id},
            match_score=0.88,
        )

    def _release_details(self, rel: dict[str, Any], track_num: str | None) -> ReleaseDetails:
        """Return the release details of one MusicBrainz release object."""
        lbl_info = (rel.get("label-info") or [{}])[0]
        media = (rel.get("media") or [{}])[0]
        return ReleaseDetails(
            release_title=rel.get("title"),
            label=(lbl_info.get("label") or {}).get("name"),
            catalog_number=lbl_info.get("catalog-number"),
            country=rel.get("country"),
            year=extract_release_year(rel.get("date")),
            track_number=track_num,
            vinyl_position=track_num,
            format_description=media.get("format"),
        )

    def _default_http_get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        """Perform HTTPS GET request to MusicBrainz API with required User-Agent."""
        return fetch_json(
            url,
            params,
            {"User-Agent": MB_USER_AGENT, "Accept": "application/json"},
            provider="MusicBrainz",
            timeout=MB_REQUEST_TIMEOUT_SEC,
            max_bytes=MAX_API_RESPONSE_BYTES,
            open_url=urllib.request.urlopen,
        )
