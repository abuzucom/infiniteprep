"""Discogs REST API adapter with 6-hour SQLite TTL cache and vinyl position parsing."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Final

from infiniteprep.adapters import http_openers
from infiniteprep.adapters.http_json import (
    RequestLimiter,
    build_candidate_id,
    cached_lookup,
    fetch_json,
)
from infiniteprep.adapters.provider_fields import (
    first_provider_text,
    provider_dict,
    provider_dicts,
    provider_text,
    provider_texts,
)
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.models import CandidateMatch, ReleaseDetails
from infiniteprep.domain.ports import MetadataSearchQuery
from infiniteprep.user_agent import USER_AGENT

DISCOGS_BASE_URL: Final[str] = "https://api.discogs.com"
DISCOGS_USER_AGENT: Final[str] = USER_AGENT
DISCOGS_TTL_SEC: Final[int] = 21600  # 6 hours per Discogs API caching rules
MAX_API_RESPONSE_BYTES: Final[int] = 5_242_880  # 5 MiB cap per API response
# Discogs release IDs are positive integers. The ID becomes a URL path segment
# in a request that carries the Discogs token. Digits keep traversal and query
# characters out of that path. IDs start at 1 and carry no leading zero.
# Sixteen digits exceed every issued release ID.
SAFE_RELEASE_ID_RE: Final[re.Pattern[str]] = re.compile(r"[1-9][0-9]{0,15}")
DISCOGS_REQUEST_TIMEOUT_SEC: Final[float] = 15.0
# A track candidate names the exact position on the release, so it scores just above a
# release found by search.
DISCOGS_SEARCH_MATCH_SCORE: Final[float] = 0.90
DISCOGS_TRACK_MATCH_SCORE: Final[float] = 0.91


def normalize_release_year(raw_year: object) -> str | None:
    """Return the release year as text, or None when Discogs reports no year.

    Discogs reports an unknown year as 0 or as an empty value.
    """
    year_text = str(raw_year).strip() if raw_year is not None else ""
    if not year_text or year_text == "0":
        return None
    return year_text


def _release_details(payload: dict[str, Any]) -> ReleaseDetails:
    """Return the release-level details of a Discogs release object.

    Each track candidate copies these details and adds its own position.
    """
    labels = provider_dicts(payload.get("labels")) or [{}]
    return ReleaseDetails(
        release_title=provider_text(payload.get("title")),
        label=provider_text(labels[0].get("name")),
        catalog_number=provider_text(labels[0].get("catno")),
        country=provider_text(payload.get("country")),
        year=normalize_release_year(provider_text(payload.get("year"))),
    )


def _track_keys(tracks: list[dict[str, Any]]) -> list[str]:
    """Return one distinct key per tracklist entry, in tracklist order.

    A heading or an index track carries an empty position and often precedes a track
    at position "1". The key of an entry without a position holds a "#" and its list
    number. A key already in the set of every earlier key, explicit or generated,
    gains the list number again until it is new, so no two entries share a key.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for index, track in enumerate(tracks):
        key = provider_text(track.get("position")) or f"#{index + 1}"
        while key in seen:
            key = f"{key}#{index + 1}"
        seen.add(key)
        keys.append(key)
    return keys


class DiscogsAdapter:
    """Queries Discogs database search and release endpoints with 6-hour TTL caching."""

    provider_name: str = "discogs"

    def __init__(
        self,
        repository: Repository,
        user_token: str | None = None,
        http_get_json: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
        min_interval_sec: float = 1.0,
    ) -> None:
        """Initialize DiscogsAdapter with Repository cache and user token."""
        self.repo = repository
        self.user_token = user_token
        self.http_get_json = http_get_json or self._default_http_get_json
        self.min_interval_sec = min_interval_sec
        self._limiter = RequestLimiter(min_interval_sec)

    def is_available(self) -> tuple[bool, str | None]:
        """Return True if a Discogs personal access token is configured."""
        if not self.user_token:
            return False, "Discogs personal access token is not configured; add it in Settings."
        return True, None

    def search(self, query: MetadataSearchQuery, file_id: str = "") -> list[CandidateMatch]:
        """Search Discogs releases matching artist, title, label, or catalog number."""
        ready, _ = self.is_available()
        if not ready:
            return []
        params: dict[str, str] = {"type": "release", "per_page": "10"}
        if query.artist:
            params["artist"] = query.artist
        if query.title:
            params["track"] = query.title
        if query.release_title:
            params["release_title"] = query.release_title
        if query.label:
            params["label"] = query.label
        if query.catalog_number:
            params["catno"] = query.catalog_number

        key_raw = json.dumps(params, sort_keys=True)
        cache_hash = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()[:24]
        cache_key = f"discogs:search:{cache_hash}"
        payload = self._cached_get(cache_key, f"{DISCOGS_BASE_URL}/database/search", params)
        if payload is None:
            return []
        results = provider_dicts(provider_dict(payload).get("results"))
        return self._parse_search_results(results, query, file_id)

    def fetch(
        self, entity_id: str, entity_type: EntityType = EntityType.RELEASE, file_id: str = ""
    ) -> list[CandidateMatch]:
        """Fetch a specific Discogs release by ID and extract tracklist positions."""
        _ = entity_type
        ready, _ = self.is_available()
        if not ready:
            return []
        if not SAFE_RELEASE_ID_RE.fullmatch(entity_id):
            raise ValueError(
                f"Invalid Discogs release id {entity_id!r}. "
                "Use 1 to 16 digits without a leading zero."
            )
        payload = self._cached_get(
            f"discogs:release:{entity_id}", f"{DISCOGS_BASE_URL}/releases/{entity_id}", {}
        )
        if payload is None:
            return []
        return self._parse_release_payload(provider_dict(payload), file_id)

    def _cached_get(
        self, cache_key: str, url: str, params: dict[str, str]
    ) -> dict[str, Any] | None:
        """Return the cached or fetched payload, or None after a failed lookup."""
        return cached_lookup(
            self.repo,
            cache_key=cache_key,
            provider=self.provider_name,
            label="Discogs",
            ttl_seconds=DISCOGS_TTL_SEC,
            fetch=lambda: self.http_get_json(url, params),
            before_request=self._respect_rate_limit,
        )

    def _respect_rate_limit(self) -> None:
        """Throttle requests to respect Discogs 60 req/min rate limit."""
        self._limiter.wait()

    def _parse_search_results(
        self,
        items: list[dict[str, Any]],
        query: MetadataSearchQuery,
        file_id: str,
    ) -> list[CandidateMatch]:
        """Convert Discogs search result entries into CandidateMatch objects."""
        return [self._candidate_from_search_item(item, query, file_id) for item in items]

    def _candidate_from_search_item(
        self,
        item: dict[str, Any],
        query: MetadataSearchQuery,
        file_id: str,
    ) -> CandidateMatch:
        """Build a single CandidateMatch from a Discogs search result dictionary.

        Every field passes the provider field checks. A wrong type reads as an absent value.
        """
        release_id = provider_text(item.get("id")) or ""
        raw_title = provider_text(item.get("title")) or ""
        artist_part, _, title_part = (
            raw_title.partition(" - ")
            if " - " in raw_title
            else (query.artist or "", "", raw_title)
        )
        formats = provider_texts(item.get("format"))
        return CandidateMatch(
            id=build_candidate_id("discogs-rel", file_id, release_id),
            file_id=file_id,
            provider=self.provider_name,
            provider_entity_id=release_id,
            entity_type=EntityType.RELEASE,
            artist=artist_part.strip() or query.artist,
            title=query.title or title_part.strip(),
            release_details=ReleaseDetails(
                release_title=title_part.strip() or None,
                label=first_provider_text(item.get("label")),
                catalog_number=provider_text(item.get("catno")),
                country=provider_text(item.get("country")),
                year=normalize_release_year(provider_text(item.get("year"))),
                genre=first_provider_text(item.get("style"))
                or first_provider_text(item.get("genre")),
                format_description=", ".join(formats) or None,
            ),
            match_evidence={"discogs_id": release_id, "raw_title": raw_title},
            match_score=DISCOGS_SEARCH_MATCH_SCORE,
        )

    def _parse_release_payload(self, payload: dict[str, Any], file_id: str) -> list[CandidateMatch]:
        """Convert a full Discogs release object with tracklist into CandidateMatches."""
        release_id = provider_text(payload.get("id")) or "unknown"
        artists = [
            provider_text(artist_credit.get("name"))
            for artist_credit in provider_dicts(payload.get("artists"))
        ]
        release_artist = ", ".join(artist_name for artist_name in artists if artist_name) or None
        release = _release_details(payload)
        results: list[CandidateMatch] = []
        tracks = provider_dicts(payload.get("tracklist"))
        for index, (track, track_key) in enumerate(zip(tracks, _track_keys(tracks), strict=True)):
            position = provider_text(track.get("position")) or f"{index + 1}"
            results.append(
                CandidateMatch(
                    id=build_candidate_id("discogs-trk", file_id, release_id, track_key),
                    file_id=file_id,
                    provider=self.provider_name,
                    # The stored candidate key holds the entity id. One id per track
                    # keeps every track of the release as its own candidate row.
                    provider_entity_id=f"{release_id}:{track_key}",
                    entity_type=EntityType.RELEASE,
                    artist=release_artist,
                    title=provider_text(track.get("title")) or "",
                    release_details=replace(
                        release, track_number=position, vinyl_position=position
                    ),
                    match_evidence={"discogs_id": release_id, "position": position},
                    match_score=DISCOGS_TRACK_MATCH_SCORE,
                )
            )
        return results

    def _default_http_get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        """Perform HTTPS GET request to Discogs API with token authentication."""
        headers = {
            "User-Agent": DISCOGS_USER_AGENT,
            "Authorization": f"Discogs token={self.user_token}",
        }
        return fetch_json(
            url,
            params,
            headers,
            provider="Discogs",
            timeout=DISCOGS_REQUEST_TIMEOUT_SEC,
            max_bytes=MAX_API_RESPONSE_BYTES,
            # The Authorization header carries the token. A redirect raises instead of
            # forwarding it to a host this URL does not name.
            open_url=http_openers.open_without_redirects,
        )
