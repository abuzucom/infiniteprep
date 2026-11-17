"""MusicBrainz ws/2 catalog metadata adapter with 1 req/sec rate limiting and SQLite cache."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Final

from infiniteprep.adapters import http_openers
from infiniteprep.adapters.provider_fields import (
    provider_dict,
    provider_dicts,
    provider_number,
    provider_text,
    require_payload_dict,
)
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.models import CandidateMatch, ReleaseDetails
from infiniteprep.domain.ports import MetadataSearchQuery
from infiniteprep.user_agent import USER_AGENT

MB_BASE_URL: Final[str] = "https://musicbrainz.org/ws/2"
MS_PER_SECOND: Final[float] = 1000.0
MAX_API_RESPONSE_BYTES: Final[int] = 5_242_880  # 5 MiB cap per API response
MB_USER_AGENT: Final[str] = USER_AGENT
MB_CACHE_TTL_SEC: Final[int] = 86400
LUCENE_SPECIAL_RE: Final[re.Pattern[str]] = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')
SAFE_ENTITY_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# A MusicBrainz release date reads YYYY, YYYY-MM, or YYYY-MM-DD. The class names ASCII
# digits, because \d also matches other Unicode decimal digits.
RELEASE_YEAR_RE: Final[re.Pattern[str]] = re.compile(r"([0-9]{4})(?:-|\Z)")


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
        self._last_request_ts: float = 0.0

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
        payload = self.repo.get_cached_response(cache_key)
        if payload is None:
            self._respect_rate_limit()
            payload = require_payload_dict(
                self.http_get_json(
                    f"{MB_BASE_URL}/recording",
                    {"query": lucene_query, "fmt": "json", "limit": "10"},
                ),
                "MusicBrainz",
            )
            self.repo.set_cached_response(
                cache_key=cache_key,
                provider=self.provider_name,
                response_data=payload,
                ttl_seconds=MB_CACHE_TTL_SEC,
            )
        recordings = provider_dicts(provider_dict(payload).get("recordings"))
        return self._parse_recording_list(recordings, file_id=file_id)

    def fetch(
        self, entity_id: str, entity_type: EntityType = EntityType.RELEASE, file_id: str = ""
    ) -> list[CandidateMatch]:
        """Fetch a specific MusicBrainz recording or release by MBID."""
        if not SAFE_ENTITY_ID_RE.match(entity_id):
            raise ValueError(f"Invalid MusicBrainz entity_id '{entity_id}'.")
        endpoint = "recording" if entity_type == EntityType.RECORDING else "release"
        cache_key = f"mb:fetch:{endpoint}:{entity_id}"
        payload = self.repo.get_cached_response(cache_key)
        if payload is None:
            self._respect_rate_limit()
            payload = require_payload_dict(
                self.http_get_json(
                    f"{MB_BASE_URL}/{endpoint}/{entity_id}",
                    {"inc": "artists+releases+labels+media", "fmt": "json"},
                ),
                "MusicBrainz",
            )
            self.repo.set_cached_response(
                cache_key=cache_key,
                provider=self.provider_name,
                response_data=payload,
                ttl_seconds=MB_CACHE_TTL_SEC,
            )
        if entity_type == EntityType.RECORDING:
            return self._parse_recording_list([provider_dict(payload)], file_id=file_id)
        return []

    def _respect_rate_limit(self) -> None:
        """Sleep if necessary to maintain the 1 request/second MusicBrainz limit."""
        if self.min_interval_sec <= 0.0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.min_interval_sec:
            time.sleep(self.min_interval_sec - elapsed)
        self._last_request_ts = time.monotonic()

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
        rec_id = provider_text(rec.get("id")) or "unknown"
        title = provider_text(rec.get("title")) or ""
        artists = [provider_text(a.get("name")) for a in provider_dicts(rec.get("artist-credit"))]
        artist_str = ", ".join(a for a in artists if a) or None
        dur_ms = provider_number(rec.get("length"))
        duration = dur_ms / MS_PER_SECOND if dur_ms else None
        releases = provider_dicts(rec.get("releases"))
        if not releases:
            return [
                CandidateMatch(
                    id=f"mb-rec-{rec_id}",
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
        rel_id = provider_text(rel.get("id")) or rec_id
        lbl_info = (provider_dicts(rel.get("label-info")) or [{}])[0]
        label_name = provider_text(provider_dict(lbl_info.get("label")).get("name"))
        cat_no = provider_text(lbl_info.get("catalog-number"))
        media = (provider_dicts(rel.get("media")) or [{}])[0]
        tracks = provider_dicts(media.get("track"))
        track_num = provider_text(tracks[0].get("number")) if tracks else None
        return CandidateMatch(
            id=f"mb-rel-{rel_id}",
            file_id=file_id,
            provider=self.provider_name,
            provider_entity_id=rel_id,
            entity_type=EntityType.RELEASE,
            artist=artist_str,
            title=title,
            duration=duration,
            release_details=ReleaseDetails(
                release_title=provider_text(rel.get("title")),
                label=label_name,
                catalog_number=cat_no,
                country=provider_text(rel.get("country")),
                year=extract_release_year(provider_text(rel.get("date"))),
                track_number=track_num,
                vinyl_position=track_num,
                format_description=provider_text(media.get("format")),
            ),
            match_evidence={"recording_id": rec_id, "release_id": rel_id},
            match_score=0.88,
        )

    def _default_http_get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        """Perform HTTPS GET request to MusicBrainz API with required User-Agent."""
        if not url.startswith("https://"):
            raise ValueError(f"MusicBrainz URL must use https:// scheme: {url}")
        encoded = urllib.parse.urlencode(params)
        req = urllib.request.Request(
            f"{url}?{encoded}",
            headers={"User-Agent": MB_USER_AGENT, "Accept": "application/json"},
        )
        # A redirect raises instead of carrying the request to a host this URL does not name.
        with http_openers.open_without_redirects(req, timeout=15) as resp:
            raw_bytes = resp.read(MAX_API_RESPONSE_BYTES + 1)
        if len(raw_bytes) > MAX_API_RESPONSE_BYTES:
            raise ValueError("MusicBrainz HTTP response exceeded maximum byte size limit.")
        return dict(json.loads(raw_bytes.decode("utf-8")))
