"""Discogs REST API adapter with 6-hour SQLite TTL cache and vinyl position parsing."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Final

from infiniteprep.adapters.provider_fields import (
    first_provider_text,
    provider_dict,
    provider_dicts,
    provider_text,
    provider_texts,
    require_payload_dict,
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
        self._last_request_ts: float = 0.0

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
        payload = self.repo.get_cached_response(cache_key)
        if payload is None:
            self._respect_rate_limit()
            payload = require_payload_dict(
                self.http_get_json(f"{DISCOGS_BASE_URL}/database/search", params), "Discogs"
            )
            self.repo.set_cached_response(
                cache_key=cache_key,
                provider=self.provider_name,
                response_data=payload,
                ttl_seconds=DISCOGS_TTL_SEC,
            )
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
        cache_key = f"discogs:release:{entity_id}"
        payload = self.repo.get_cached_response(cache_key)
        if payload is None:
            self._respect_rate_limit()
            payload = require_payload_dict(
                self.http_get_json(f"{DISCOGS_BASE_URL}/releases/{entity_id}", {}), "Discogs"
            )
            self.repo.set_cached_response(
                cache_key=cache_key,
                provider=self.provider_name,
                response_data=payload,
                ttl_seconds=DISCOGS_TTL_SEC,
            )
        return self._parse_release_payload(provider_dict(payload), file_id)

    def _respect_rate_limit(self) -> None:
        """Throttle requests to respect Discogs 60 req/min rate limit."""
        if self.min_interval_sec <= 0.0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.min_interval_sec:
            time.sleep(self.min_interval_sec - elapsed)
        self._last_request_ts = time.monotonic()

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
        rel_id = provider_text(item.get("id")) or ""
        raw_title = provider_text(item.get("title")) or ""
        artist_part, _, title_part = (
            raw_title.partition(" - ")
            if " - " in raw_title
            else (query.artist or "", "", raw_title)
        )
        formats = provider_texts(item.get("format"))
        return CandidateMatch(
            id=f"discogs-rel-{rel_id}",
            file_id=file_id,
            provider=self.provider_name,
            provider_entity_id=rel_id,
            entity_type=EntityType.RELEASE,
            artist=artist_part.strip() or query.artist,
            title=query.title or title_part.strip(),
            release_details=ReleaseDetails(
                release_title=title_part.strip() or None,
                label=first_provider_text(item.get("label")),
                catalog_number=provider_text(item.get("catno")),
                country=provider_text(item.get("country")),
                year=provider_text(item.get("year")),
                genre=first_provider_text(item.get("style"))
                or first_provider_text(item.get("genre")),
                format_description=", ".join(formats) or None,
            ),
            match_evidence={"discogs_id": rel_id, "raw_title": raw_title},
            match_score=0.90,
        )

    def _parse_release_payload(self, payload: dict[str, Any], file_id: str) -> list[CandidateMatch]:
        """Convert a full Discogs release object with tracklist into CandidateMatches."""
        rel_id = provider_text(payload.get("id")) or "unknown"
        labels = provider_dicts(payload.get("labels")) or [{}]
        label_name = provider_text(labels[0].get("name"))
        cat_no = provider_text(labels[0].get("catno"))
        artists = [provider_text(a.get("name")) for a in provider_dicts(payload.get("artists"))]
        rel_artist = ", ".join(a for a in artists if a) or None
        release_title = provider_text(payload.get("title"))
        country = provider_text(payload.get("country"))
        year = provider_text(payload.get("year"))
        results: list[CandidateMatch] = []
        for idx, trk in enumerate(provider_dicts(payload.get("tracklist"))):
            pos = provider_text(trk.get("position")) or f"{idx + 1}"
            results.append(
                CandidateMatch(
                    id=f"discogs-trk-{rel_id}-{pos}",
                    file_id=file_id,
                    provider=self.provider_name,
                    provider_entity_id=rel_id,
                    entity_type=EntityType.RELEASE,
                    artist=rel_artist,
                    title=provider_text(trk.get("title")) or "",
                    release_details=ReleaseDetails(
                        release_title=release_title,
                        label=label_name,
                        catalog_number=cat_no,
                        country=country,
                        year=year,
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
        if not url.startswith("https://"):
            raise ValueError(f"Discogs URL must use https:// scheme: {url}")
        encoded = urllib.parse.urlencode(params)
        full_url = f"{url}?{encoded}" if encoded else url
        headers = {
            "User-Agent": DISCOGS_USER_AGENT,
            "Authorization": f"Discogs token={self.user_token}",
        }
        req = urllib.request.Request(full_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_bytes = resp.read(MAX_API_RESPONSE_BYTES + 1)
        if len(raw_bytes) > MAX_API_RESPONSE_BYTES:
            raise ValueError("Discogs HTTP response exceeded maximum byte size limit.")
        return dict(json.loads(raw_bytes.decode("utf-8")))
