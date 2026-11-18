"""Chromaprint (fpcalc) and AcoustID fingerprint lookup adapter (Section 7.B)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from infiniteprep.adapters import http_openers
from infiniteprep.adapters.fingerprint.fpcalc_bootstrap import FpcalcBootstrapService
from infiniteprep.adapters.http_json import LOOKUP_ERRORS, fetch_json, log_lookup_failure
from infiniteprep.adapters.provider_fields import (
    provider_dict,
    provider_dicts,
    provider_number,
    provider_text,
    require_payload_dict,
)
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.ports import FingerprintMatchItem, FingerprintResult
from infiniteprep.user_agent import USER_AGENT

ACOUSTID_API_URL: Final[str] = "https://api.acoustid.org/v2/lookup"
CACHE_TTL_SEC: Final[int] = 86400
MAX_API_RESPONSE_BYTES: Final[int] = 5_242_880  # 5 MiB cap per API response
ACOUSTID_REQUEST_TIMEOUT_SEC: Final[float] = 15.0
# fpcalc reads at most the first two minutes of audio and finishes in a few seconds. The
# timeout stops a hung decoder.
FPCALC_TIMEOUT_SEC: Final[int] = 30
# fpcalc fails with a missing binary or unreadable file (OSError), a nonzero exit or
# a timeout (SubprocessError), or output that is not the expected JSON (ValueError).
FPCALC_ERRORS: Final[tuple[type[Exception], ...]] = (
    OSError,
    ValueError,
    subprocess.SubprocessError,
)


class ChromaprintAcoustIDAdapter:
    """Computes local Chromaprint fingerprints via fpcalc and queries AcoustID API."""

    provider_name: str = "acoustid"
    is_cloud_opt_in: bool = False

    def __init__(
        self,
        repository: Repository,
        api_key: str | None = None,
        bootstrap_service: FpcalcBootstrapService | None = None,
        fpcalc_runner: Callable[[Path], tuple[float, str]] | None = None,
        http_get_json: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
    ) -> None:
        """Initialize ChromaprintAcoustIDAdapter with SQLite cache and optional test hooks."""
        self.repo = repository
        self.api_key = api_key
        self.bootstrap = bootstrap_service or FpcalcBootstrapService()
        self.fpcalc_runner = fpcalc_runner
        self.http_get_json = http_get_json or self._default_http_get_json

    def is_available(self) -> tuple[bool, str | None]:
        """Return True if fpcalc binary and AcoustID API key are available."""
        if not self.api_key:
            return False, "AcoustID API key is not configured; add it in Settings."
        if self.fpcalc_runner is None and self.bootstrap.resolve_fpcalc_path() is None:
            return False, "Chromaprint fpcalc binary is missing; run consented setup in Settings."
        return True, None

    def identify(self, audio_reference: Path, file_id: str | None = None) -> FingerprintResult:
        """Compute Chromaprint fingerprint and lookup recording candidates on AcoustID."""
        _ = file_id
        ready, reason = self.is_available()
        if not ready:
            return FingerprintResult(
                provider=self.provider_name,
                raw_fingerprint=None,
                duration=None,
                matches=[],
                warnings=[reason or "AcoustID adapter unavailable."],
            )
        try:
            duration, fingerprint = self._run_fpcalc(audio_reference)
        except FPCALC_ERRORS as exc:
            return FingerprintResult(
                provider=self.provider_name,
                raw_fingerprint=None,
                duration=None,
                matches=[],
                warnings=[f"fpcalc execution failed ({exc}); check audio file or fpcalc binary."],
            )

        try:
            payload = self._lookup_acoustid_cached(duration, fingerprint)
        except LOOKUP_ERRORS as error:
            # A failed lookup fails this file only. The job continues.
            return FingerprintResult(
                provider=self.provider_name,
                raw_fingerprint=fingerprint,
                duration=duration,
                matches=[],
                warnings=[log_lookup_failure("AcoustID", error)],
            )
        matches = self._parse_acoustid_results(payload)
        return FingerprintResult(
            provider=self.provider_name,
            raw_fingerprint=fingerprint,
            duration=duration,
            matches=matches,
            warnings=[],
        )

    def _run_fpcalc(self, audio_reference: Path) -> tuple[float, str]:
        """Execute fpcalc safely using array arguments (shell=False) and parse output."""
        if self.fpcalc_runner is not None:
            return self.fpcalc_runner(audio_reference)
        fpcalc_bin = self.bootstrap.resolve_fpcalc_path()
        if fpcalc_bin is None:
            raise FileNotFoundError("fpcalc binary not found; install fpcalc in Settings.")
        completed = subprocess.run(
            [str(fpcalc_bin), "-json", str(audio_reference)],
            capture_output=True,
            text=True,
            timeout=FPCALC_TIMEOUT_SEC,
            check=True,
            shell=False,
        )
        parsed = json.loads(completed.stdout)
        return float(parsed.get("duration", 0.0)), str(parsed.get("fingerprint", ""))

    def _lookup_acoustid_cached(self, duration: float, fingerprint: str) -> dict[str, Any]:
        """Query AcoustID with SQLite provider_cache backing."""
        fingerprint_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
        cache_key = f"acoustid:{int(duration)}:{fingerprint_hash}"
        cached = self.repo.get_cached_response(cache_key)
        if cached is not None:
            return cached
        params = {
            "client": self.api_key or "",
            "meta": "recordings+releasegroups+compress",
            "duration": str(max(1, round(duration))),
            "fingerprint": fingerprint,
        }
        response = require_payload_dict(self.http_get_json(ACOUSTID_API_URL, params), "AcoustID")
        self.repo.set_cached_response(
            cache_key=cache_key,
            provider=self.provider_name,
            response_data=response,
            ttl_seconds=CACHE_TTL_SEC,
        )
        return response

    def _parse_acoustid_results(self, payload: dict[str, Any]) -> list[FingerprintMatchItem]:
        """Convert AcoustID JSON results into FingerprintMatchItem domain objects."""
        items: list[FingerprintMatchItem] = []
        for lookup_result in provider_dicts(provider_dict(payload).get("results")):
            # AcoustID scores lie in [0, 1]. A value outside that range reads as no confidence.
            score = provider_number(lookup_result.get("score")) or 0.0
            score = score if score <= 1.0 else 0.0
            acid_id = provider_text(lookup_result.get("id")) or ""
            for recording in provider_dicts(lookup_result.get("recordings")):
                items.append(self._build_match_item(acid_id, score, recording))
        return items

    def _build_match_item(
        self, acid_id: str, score: float, recording_data: dict[str, Any]
    ) -> FingerprintMatchItem:
        """Build a single FingerprintMatchItem from an AcoustID recording dictionary."""
        recording_id = provider_text(recording_data.get("id")) or ""
        names = (
            provider_text(artist_entry.get("name"))
            for artist_entry in provider_dicts(recording_data.get("artists"))
        )
        artists = [name for name in names if name]
        group_ids = (
            provider_text(release_group.get("id"))
            for release_group in provider_dicts(recording_data.get("releasegroups"))
        )
        release_group_ids = [group_id for group_id in group_ids if group_id]
        title = provider_text(recording_data.get("title"))
        duration = provider_number(recording_data.get("duration"))
        # The evidence holds only the checked fields. The whole recording object can carry
        # any nested shape and text up to the response cap, and the evidence is stored.
        recording = {
            "id": recording_id,
            "title": title,
            "duration": duration,
            "artists": artists,
            "releasegroup_ids": release_group_ids,
        }
        return FingerprintMatchItem(
            fingerprint_id=acid_id,
            confidence=score,
            recording_ids=[recording_id] if recording_id else [],
            artist=", ".join(artists) if artists else None,
            title=title,
            duration=duration,
            release_group_ids=release_group_ids,
            raw_evidence={"acoustid_id": acid_id, "recording": recording},
        )

    def _default_http_get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        """Perform HTTPS GET request to AcoustID API."""
        return fetch_json(
            url,
            params,
            {"User-Agent": USER_AGENT},
            provider="AcoustID",
            timeout=ACOUSTID_REQUEST_TIMEOUT_SEC,
            max_bytes=MAX_API_RESPONSE_BYTES,
            # The query string carries the API key. A redirect raises instead of
            # forwarding it to a host this URL does not name.
            open_url=http_openers.open_without_redirects,
        )
