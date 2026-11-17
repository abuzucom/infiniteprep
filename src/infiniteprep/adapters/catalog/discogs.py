"""Discogs REST API adapter with 6-hour SQLite TTL cache and vinyl position parsing."""

from __future__ import annotations

import hashlib
import json
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


def normalize_release_year(raw_year: object) -> str | None:
    """Return the release year as text, or None when Discogs reports no year.

    Discogs reports an unknown year as 0 or as an empty value.
    """
    year_text = str(raw_year).strip() if raw_year is not None else ""
    if not year_text or year_text == "0":
        return None
    return year_text


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
        return self._parse_search_results(payload.get("results", []), query, file_id)

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
        return self._parse_release_payload(payload, file_id)

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
            log_lookup_failure("Discogs", error)
            return None
        self.repo.set_cached_response(
            cache_key=cache_key,
            provider=self.provider_name,
            response_data=payload,
            ttl_seconds=DISCOGS_TTL_SEC,
        )
        return payload

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
        """Build a single CandidateMatch from a Discogs search result dictionary."""
        rel_id = str(item.get("id", ""))
        raw_title = str(item.get("title", ""))
        artist_part, _, title_part = (
            raw_title.partition(" - ")
            if " - " in raw_title
            else (query.artist or "", "", raw_title)
        )
        labels = item.get("label") or []
        formats = item.get("format") or []
        genres = item.get("style") or item.get("genre") or []
        return CandidateMatch(
            id=build_candidate_id("discogs-rel", file_id, rel_id),
            file_id=file_id,
            provider=self.provider_name,
            provider_entity_id=rel_id,
            entity_type=EntityType.RELEASE,
            artist=artist_part.strip() or query.artist,
            title=query.title or title_part.strip(),
            release_details=ReleaseDetails(
                release_title=title_part.strip() or None,
                label=str(labels[0]) if isinstance(labels, list) and labels else None,
                catalog_number=item.get("catno"),
                country=item.get("country"),
                year=normalize_release_year(item.get("year")),
                genre=str(genres[0]) if isinstance(genres, list) and genres else None,
                format_description=(
                    ", ".join(str(f) for f in formats) if isinstance(formats, list) else None
                ),
            ),
            match_evidence={"discogs_id": rel_id, "raw_title": raw_title},
            match_score=0.90,
        )

    def _parse_release_payload(self, payload: dict[str, Any], file_id: str) -> list[CandidateMatch]:
        """Convert a full Discogs release object with tracklist into CandidateMatches."""
        rel_id = str(payload.get("id", "unknown"))
        labels = payload.get("labels") or [{}]
        label_name = labels[0].get("name")
        cat_no = labels[0].get("catno")
        artists = [a.get("name", "") for a in payload.get("artists", []) if isinstance(a, dict)]
        rel_artist = ", ".join(a for a in artists if a) or None
        tracks = payload.get("tracklist") or []
        results: list[CandidateMatch] = []
        for idx, trk in enumerate(tracks):
            # A heading or an index track carries an empty position. The list index
            # keeps its candidate id and its entity id distinct from the other tracks.
            pos = str(trk.get("position") or "").strip() or str(idx + 1)
            results.append(
                CandidateMatch(
                    id=build_candidate_id("discogs-trk", file_id, rel_id, pos),
                    file_id=file_id,
                    provider=self.provider_name,
                    # The stored candidate key holds the entity id. One id per track
                    # keeps every track of the release as its own candidate row.
                    provider_entity_id=f"{rel_id}:{pos}",
                    entity_type=EntityType.RELEASE,
                    artist=rel_artist,
                    title=str(trk.get("title", "")),
                    release_details=ReleaseDetails(
                        release_title=payload.get("title"),
                        label=label_name,
                        catalog_number=cat_no,
                        country=payload.get("country"),
                        year=normalize_release_year(payload.get("year")),
                        track_number=pos,
                        vinyl_position=pos,
                    ),
                    match_evidence={"discogs_id": rel_id, "position": pos},
                    match_score=0.91,
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
            open_url=urllib.request.urlopen,
        )
