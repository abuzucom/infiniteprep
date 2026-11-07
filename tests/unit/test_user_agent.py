"""Behavioral tests for the shared User-Agent on every outbound HTTP request (D221)."""

from __future__ import annotations

import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from infiniteprep import __version__
from infiniteprep.adapters.catalog.discogs import DiscogsAdapter
from infiniteprep.adapters.catalog.musicbrainz import MusicBrainzAdapter
from infiniteprep.adapters.fingerprint.chromaprint_acoustid import ChromaprintAcoustIDAdapter
from infiniteprep.adapters.fingerprint.fpcalc_bootstrap import FpcalcBootstrapService
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.user_agent import PROJECT_URL, USER_AGENT

STUB_BODY = b"{}"
REQUEST_URL = "https://example.invalid/api"


class _StubResponse:
    """Stand in for an HTTP response with a fixed JSON body."""

    def __enter__(self) -> _StubResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        """Return the stub body."""
        return STUB_BODY


@pytest.fixture
def sent_requests(monkeypatch: pytest.MonkeyPatch) -> list[urllib.request.Request]:
    """Replace urlopen so no request leaves the machine, and record each request."""
    recorded: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _StubResponse:
        assert timeout > 0
        recorded.append(request)
        return _StubResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return recorded


def _repository() -> Repository:
    """Return a Repository on a fresh in-memory database."""
    return Repository(DatabaseManager(":memory:"))


def _musicbrainz_get() -> Any:
    return MusicBrainzAdapter(_repository()).http_get_json(REQUEST_URL, {})


def _discogs_get() -> Any:
    return DiscogsAdapter(_repository(), user_token="test-token").http_get_json(REQUEST_URL, {})


def _acoustid_get() -> Any:
    adapter = ChromaprintAcoustIDAdapter(_repository(), api_key="test-key")
    return adapter.http_get_json(REQUEST_URL, {})


def _fpcalc_download(tmp_path: Path) -> Any:
    return FpcalcBootstrapService(install_dir=tmp_path).downloader(REQUEST_URL)


def test_user_agent_names_version_and_repository() -> None:
    """The User-Agent carries the app version and the real repository URL, and no email."""
    assert USER_AGENT == f"InfinitePrep/{__version__} (+{PROJECT_URL})"
    assert PROJECT_URL == "https://github.com/abuzucom/infiniteprep"
    assert "@" not in USER_AGENT


@pytest.mark.parametrize(
    "send_request",
    [
        lambda _tmp: _musicbrainz_get(),
        lambda _tmp: _discogs_get(),
        lambda _tmp: _acoustid_get(),
        _fpcalc_download,
    ],
    ids=["musicbrainz", "discogs", "acoustid", "fpcalc-download"],
)
def test_every_site_sends_the_shared_user_agent(
    sent_requests: list[urllib.request.Request],
    tmp_path: Path,
    send_request: Callable[[Path], Any],
) -> None:
    """MusicBrainz, Discogs, AcoustID, and the fpcalc download all send USER_AGENT."""
    send_request(tmp_path)

    [request] = sent_requests
    assert request.get_header("User-agent") == USER_AGENT
