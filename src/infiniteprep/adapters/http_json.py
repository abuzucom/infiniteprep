"""Shared HTTPS JSON fetch and request pacing for the online metadata adapters.

MusicBrainz, Discogs, iTunes Search, and AcoustID each held a copy of the bounded GET and
of the request pacing. A fix to one copy reached that adapter only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Final, Protocol

from infiniteprep.adapters.provider_fields import require_payload_dict

MAX_API_RESPONSE_BYTES: Final[int] = 5_242_880  # 5 MiB cap per API response
DEFAULT_REQUEST_TIMEOUT_SEC: Final[float] = 15.0
# A lookup that fails on the network or returns malformed JSON fails that lookup only.
# URLError, HTTPError, and socket timeouts are OSError subclasses. A JSON decode error
# and an oversized response are ValueError.
LOOKUP_ERRORS: Final[tuple[type[Exception], ...]] = (OSError, ValueError)

logger = logging.getLogger(__name__)
# Percent-encoding turns ":" inside a part into "%3A", so the separator stays unique.
CANDIDATE_ID_SEPARATOR: Final[str] = ":"


def build_candidate_id(prefix: str, file_id: str, *entity_ids: str) -> str:
    """Return a candidate id unique per file and per provider entity.

    The id holds the file id, because the same recording found for two files
    must stay two candidate rows. Each part is percent-encoded before the join,
    so the separator never appears inside a part and two different part tuples
    never yield the same id. An empty part keeps its slot.
    """
    parts = (prefix, file_id, *entity_ids)
    return CANDIDATE_ID_SEPARATOR.join(urllib.parse.quote(part, safe="") for part in parts)


def log_lookup_failure(provider: str, error: Exception) -> str:
    """Log one failed provider lookup and return the warning text for the caller.

    The text holds the provider and the error type only. A response body or a URL
    with a query string can carry a credential, so neither reaches the log.
    """
    warning = f"{provider} lookup failed ({type(error).__name__}); the provider returned no result."
    logger.warning(warning)
    return warning


class ResponseCache(Protocol):
    """The provider response cache that the Repository offers."""

    def get_cached_response(self, cache_key: str) -> dict[str, Any] | None:
        """Return the cached response, or None when absent or expired."""
        ...

    def set_cached_response(
        self, *, cache_key: str, provider: str, response_data: dict[str, Any], ttl_seconds: int
    ) -> None:
        """Store a response under cache_key for ttl_seconds."""
        ...


def cached_lookup(
    cache: ResponseCache,
    *,
    cache_key: str,
    provider: str,
    label: str,
    ttl_seconds: int,
    fetch: Callable[[], object],
    before_request: Callable[[], None] | None = None,
    cache_form: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return the cached or fetched JSON object, or None after a failed lookup.

    Every online adapter runs this one sequence: cache check, request pacing,
    fetch, JSON object check, and cache write. A network failure or a malformed
    response fails this lookup only, is logged under label, and stays out of the
    cache. before_request runs only when the cache misses. cache_form selects the
    part of the response that the cache keeps. The caller gets the full response.
    """
    cached = cache.get_cached_response(cache_key)
    if cached is not None:
        return cached
    if before_request is not None:
        before_request()
    try:
        payload = require_payload_dict(fetch(), label)
    except LOOKUP_ERRORS as error:
        log_lookup_failure(label, error)
        return None
    stored = cache_form(payload) if cache_form is not None else payload
    cache.set_cached_response(
        cache_key=cache_key, provider=provider, response_data=stored, ttl_seconds=ttl_seconds
    )
    return payload


class RequestLimiter:
    """Holds the request timing for one provider.

    The lock keeps two callers from passing the interval check at the same time.
    """

    def __init__(self, min_interval_sec: float) -> None:
        """Initialize the limiter with the minimum interval between requests."""
        self.min_interval_sec = min_interval_sec
        self._lock = threading.Lock()
        self._last_request_ts: float = 0.0

    def wait(self) -> None:
        """Hold the next request until the minimum interval has passed."""
        if self.min_interval_sec <= 0.0:
            return
        with self._lock:
            self._hold_interval()
            self._last_request_ts = time.monotonic()

    @contextmanager
    def request_slot(self) -> Iterator[None]:
        """Hold the provider slot for one request.

        The caller runs the cache check and the request inside the slot. A second
        caller waits for the slot and then finds the cached response, so two
        threads never send the same lookup twice.
        """
        with self._lock:
            if self.min_interval_sec > 0.0:
                self._hold_interval()
                self._last_request_ts = time.monotonic()
            yield

    def _hold_interval(self) -> None:
        """Sleep until the minimum interval has passed since the last request."""
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.min_interval_sec:
            time.sleep(self.min_interval_sec - elapsed)


def fetch_json(
    url: str,
    params: Mapping[str, str],
    headers: Mapping[str, str],
    *,
    provider: str,
    timeout: float = DEFAULT_REQUEST_TIMEOUT_SEC,
    max_bytes: int = MAX_API_RESPONSE_BYTES,
    open_url: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """GET url over HTTPS and return the JSON object, reading at most max_bytes.

    The read stops one byte past max_bytes, so an oversized response never enters
    memory in full. provider names the service in each error message. open_url
    defaults to urllib.request.urlopen, looked up at call time.
    """
    if not url.startswith("https://"):
        raise ValueError(f"{provider} URL must use https:// scheme: {url}")
    encoded = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{encoded}" if encoded else url, headers=dict(headers))
    opener = open_url if open_url is not None else urllib.request.urlopen
    with opener(request, timeout=timeout) as response:
        raw_bytes = response.read(max_bytes + 1)
    if len(raw_bytes) > max_bytes:
        raise ValueError(f"{provider} HTTP response exceeded maximum byte size limit.")
    decoded = json.loads(raw_bytes.decode("utf-8"))
    # Every caller reads named fields. Any other JSON shape counts as a malformed
    # response, which LOOKUP_ERRORS covers as ValueError.
    if not isinstance(decoded, dict):
        raise ValueError(f"{provider} HTTP response is not a JSON object.")
    return decoded
