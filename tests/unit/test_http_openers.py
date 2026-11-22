"""Tests that credentialed provider requests never follow a redirect."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from infiniteprep.adapters import http_openers
from infiniteprep.adapters.catalog import discogs as discogs_module
from infiniteprep.adapters.catalog import musicbrainz as musicbrainz_module
from infiniteprep.adapters.fingerprint import chromaprint_acoustid as acoustid_module
from infiniteprep.adapters.llm import ollama_openai as ollama_openai_module
from tests.fixtures.memory_repository import build_memory_repository

# A structurally fake key. The recording stub never sends it anywhere.
STUB_KEY = "synthetic"
REDIRECT_STATUS = 302
OK_STATUS = 200
LOOPBACK = "127.0.0.1"
TARGET_PATH = "/target"
JOIN_TIMEOUT_SEC = 5.0
# shutdown() waits for the next poll. The default 0.5 s poll would add that wait.
SERVER_POLL_SEC = 0.01


class _RedirectHandler(BaseHTTPRequestHandler):
    """Redirect every request to /target and count the requests that reach /target."""

    target_hits: list[str]

    def do_GET(self) -> None:
        self._answer()

    def do_POST(self) -> None:
        self._answer()

    def _answer(self) -> None:
        if self.path.startswith(TARGET_PATH):
            self.target_hits.append(self.headers.get("Authorization", ""))
            self.send_response(OK_STATUS)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.send_response(REDIRECT_STATUS)
        self.send_header("Location", TARGET_PATH)
        self.end_headers()

    def log_message(self, *_args: Any) -> None:
        """Keep test output quiet."""


@pytest.fixture
def redirect_server() -> Iterator[tuple[str, list[str]]]:
    """Run a local server that redirects, and yield its base URL and the target hits."""
    hits: list[str] = []
    handler = type("Handler", (_RedirectHandler,), {"target_hits": hits})
    server = ThreadingHTTPServer((LOOPBACK, 0), handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": SERVER_POLL_SEC}, daemon=True
    )
    thread.start()
    try:
        yield f"http://{LOOPBACK}:{server.server_address[1]}", hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(JOIN_TIMEOUT_SEC)


def test_no_redirect_opener_raises_on_a_redirect(redirect_server: tuple[str, list[str]]) -> None:
    """A redirect response raises, and the redirect target never receives the request."""
    base_url, hits = redirect_server
    request = urllib.request.Request(
        f"{base_url}/start", headers={"Authorization": "Bearer placeholder"}
    )

    with pytest.raises(urllib.error.HTTPError) as raised:
        http_openers.open_without_redirects(request, timeout=JOIN_TIMEOUT_SEC)

    assert raised.value.code == REDIRECT_STATUS
    assert hits == []


def test_no_redirect_opener_returns_a_direct_response(
    redirect_server: tuple[str, list[str]],
) -> None:
    """A request that needs no redirect returns its body as before."""
    base_url, hits = redirect_server

    with http_openers.open_without_redirects(
        urllib.request.Request(f"{base_url}{TARGET_PATH}"), timeout=JOIN_TIMEOUT_SEC
    ) as response:
        body = response.read()

    assert body == b"{}"
    assert len(hits) == 1


class _StubResponse:
    """Stands in for an HTTP response with an empty JSON object body."""

    def __enter__(self) -> _StubResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        """Return an empty JSON object."""
        return b"{}"


def _discogs_send() -> Any:
    adapter = discogs_module.DiscogsAdapter(build_memory_repository(), user_token=STUB_KEY)
    return adapter._default_http_get_json(f"{discogs_module.DISCOGS_BASE_URL}/releases/1", {})


def _musicbrainz_send() -> Any:
    adapter = musicbrainz_module.MusicBrainzAdapter(build_memory_repository())
    return adapter._default_http_get_json(f"{musicbrainz_module.MB_BASE_URL}/recording", {})


def _acoustid_send() -> Any:
    adapter = acoustid_module.ChromaprintAcoustIDAdapter(
        build_memory_repository(), api_key=STUB_KEY
    )
    return adapter._default_http_get_json("https://api.acoustid.org/v2/lookup", {})


def _ollama_send() -> Any:
    adapter = ollama_openai_module.OllamaOpenAIReasoningAdapter(api_key=STUB_KEY)
    return adapter._default_http_post_json(
        "http://127.0.0.1:11434/v1/chat/completions", {"model": "qwen2.5:3b"}
    )


@pytest.mark.parametrize(
    "send",
    [_discogs_send, _musicbrainz_send, _acoustid_send, _ollama_send],
    ids=["discogs", "musicbrainz", "acoustid", "ollama-openai"],
)
def test_credentialed_use_no_redirect_opener(monkeypatch: pytest.MonkeyPatch, send: Any) -> None:
    """Each credentialed adapter sends exactly one request through the shared opener."""
    sent: list[urllib.request.Request] = []

    def record(request: urllib.request.Request, timeout: float) -> _StubResponse:
        assert timeout > 0
        sent.append(request)
        return _StubResponse()

    def refuse_urlopen(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("A credentialed adapter called urlopen, which follows redirects.")

    monkeypatch.setattr(http_openers, "open_without_redirects", record)
    monkeypatch.setattr(urllib.request, "urlopen", refuse_urlopen)

    assert send() == {}
    assert len(sent) == 1
