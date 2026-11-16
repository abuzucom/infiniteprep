"""Behavioral tests for the iTunes Search request policy (D404).

The endpoint is a published constant. The adapter follows no redirect, shares
one limiter across every instance, and keeps a response for one endpoint out
of another provider cache.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.catalog.itunes import (
    ITUNES_SEARCH_HOST,
    ITUNES_SEARCH_URL,
    ItunesSearchAdapter,
    RequestLimiter,
    build_search_opener,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.ports import MetadataSearchQuery

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "itunes_search"
FILE_ID = "file-1"


def _recorded(name: str) -> dict[str, Any]:
    """Return the recorded iTunes response stored under the fixture directory."""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _repository() -> Repository:
    """Return a Repository on a fresh in-memory database."""
    return Repository(DatabaseManager(":memory:"))


def _adapter(
    repository: Repository,
    payload: dict[str, Any],
    calls: list[tuple[str, dict[str, str]]] | None = None,
    limiter: RequestLimiter | None = None,
) -> ItunesSearchAdapter:
    """Return an adapter that replays a recorded response instead of calling the network."""

    def fake_get(url: str, params: dict[str, str]) -> dict[str, Any]:
        if calls is not None:
            calls.append((url, params))
        return payload

    return ItunesSearchAdapter(
        repository=repository,
        http_get_json=fake_get,
        limiter=limiter or RequestLimiter(0.0),
    )


def test_the_endpoint_is_the_published_https_search_url() -> None:
    """The adapter uses the published iTunes Search endpoint over HTTPS."""
    parsed = urllib.parse.urlsplit(ITUNES_SEARCH_URL)

    assert parsed.scheme == "https"
    assert parsed.hostname == ITUNES_SEARCH_HOST
    assert parsed.path == "/search"


def test_the_request_path_refuses_a_redirect() -> None:
    """The opener follows no redirect, so a lookup never leaves the endpoint."""
    opener = build_search_opener()

    assert not any(isinstance(item, urllib.request.HTTPRedirectHandler) for item in opener.handlers)


def test_a_redirect_response_becomes_an_error_and_never_reaches_the_target() -> None:
    """A redirect response raises and the target path never receives a request."""
    requested_paths: list[str] = []

    class RedirectHandler(BaseHTTPRequestHandler):
        """Answers the first path with a redirect and the second with a body."""

        def do_GET(self) -> None:
            """Answer one request."""
            requested_paths.append(self.path)
            if self.path == "/search":
                self.send_response(302)
                self.send_header("Location", "/target")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format: str, *args: Any) -> None:
            """Keep the test output free of the server log."""

    server = HTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        opener = build_search_opener()
        url = f"http://127.0.0.1:{server.server_port}/search"
        with pytest.raises(urllib.error.HTTPError) as error:
            opener.open(url, timeout=5)
        assert error.value.code == 302
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert "/target" not in requested_paths


def test_the_request_path_refuses_a_url_outside_the_published_endpoint() -> None:
    """The default request path rejects any URL that is not the published endpoint."""
    adapter = ItunesSearchAdapter(repository=_repository(), limiter=RequestLimiter(0.0))

    with pytest.raises(ValueError, match="published endpoint"):
        adapter._default_http_get_json("https://127.0.0.1/search", {})


def test_every_adapter_shares_the_provider_limiter() -> None:
    """Two adapters in the process wait for the same interval."""
    first = ItunesSearchAdapter(repository=_repository())
    second = ItunesSearchAdapter(repository=_repository())

    assert first.limiter is second.limiter


def test_two_adapters_that_share_a_limiter_wait_for_each_other() -> None:
    """A second adapter waits for the interval the first adapter used."""
    limiter = RequestLimiter(0.05)
    repository = _repository()
    calls: list[tuple[str, dict[str, str]]] = []
    first = _adapter(repository, _recorded("match_with_genre_and_date"), calls, limiter)
    second = _adapter(repository, _recorded("match_with_genre_and_date"), calls, limiter)

    first.search(MetadataSearchQuery(artist="Deep Dish", title="The Dream"), file_id=FILE_ID)
    second.search(MetadataSearchQuery(artist="Deep Dish", title="Flashdance"), file_id=FILE_ID)

    assert len(calls) == 2
    assert calls[0][0] == ITUNES_SEARCH_URL
    assert calls[1][0] == ITUNES_SEARCH_URL
