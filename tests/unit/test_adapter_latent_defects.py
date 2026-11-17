"""Behavioral tests for the latent adapter defects fixed in phase 4 of issue 288."""

from __future__ import annotations

import io
import logging
import subprocess
import urllib.error
from pathlib import Path
from typing import Any, cast

import pytest

from infiniteprep.adapters import http_json
from infiniteprep.adapters.catalog.acrcloud_optin import ACRCloudOptInAdapter
from infiniteprep.adapters.catalog.beatport_stub import BeatportStubAdapter
from infiniteprep.adapters.catalog.discogs import DiscogsAdapter
from infiniteprep.adapters.catalog.itunes import ItunesSearchAdapter, RequestLimiter
from infiniteprep.adapters.catalog.musicbrainz import MusicBrainzAdapter
from infiniteprep.adapters.fingerprint.chromaprint_acoustid import ChromaprintAcoustIDAdapter
from infiniteprep.adapters.llm import ollama_bootstrap as ollama_module
from infiniteprep.adapters.llm.ollama_bootstrap import OllamaBootstrapService, build_tags_url
from infiniteprep.adapters.llm.validator import ClosedWorldValidator
from infiniteprep.config.credentials import (
    ENV_VAR_MAP,
    FORGET_FAILED_EXIT_CODE,
    CredentialStore,
    CredentialStoreError,
    forget_all_credentials,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.ports import MetadataSearchQuery

DISCOGS_STUB_KEY = "synthetic-discogs-key"
ACOUSTID_TEST_KEY = "synthetic-acoustid-key"
BEATPORT_STUB_KEY = "synthetic-beatport-key"
ACRCLOUD_STUB_KEY = "synthetic-acrcloud-key"
ACRCLOUD_STUB_VALUE = "synthetic-acrcloud-value"
FIRST_FILE_ID = "file-one"
SECOND_FILE_ID = "file-two"
QUERY = MetadataSearchQuery(artist="Deep Dish", title="The Dream")

MB_SEARCH_PAYLOAD: dict[str, Any] = {
    "recordings": [
        {
            "id": "rec-1",
            "title": "The Dream",
            "artist-credit": [{"name": "Deep Dish"}],
            "releases": [{"id": "rel-1", "title": "Junk Science", "media": [{"track": [{}]}]}],
        }
    ]
}
MB_RELEASE_PAYLOAD: dict[str, Any] = {
    "id": "rel-9",
    "title": "Junk Science",
    "date": "1998-06-01",
    "country": "GB",
    "artist-credit": [{"name": "Deep Dish"}],
    "label-info": [{"catalog-number": "DD001", "label": {"name": "Deconstruction"}}],
    "media": [{"format": "Vinyl"}],
}
DISCOGS_RELEASE_PAYLOAD: dict[str, Any] = {
    "id": 249504,
    "title": "Junk Science",
    "year": 0,
    "artists": [{"name": "Deep Dish"}],
    "labels": [{"name": "Deconstruction", "catno": "DD001"}],
    "tracklist": [
        {"position": "", "title": "Side A"},
        {"position": "A1", "title": "The Dream"},
        {"position": "", "title": "Side B"},
    ],
}
ITUNES_PAYLOAD: dict[str, Any] = {
    "resultCount": 1,
    "results": [
        {
            "trackId": 123456789,
            "artistName": "Deep Dish",
            "trackName": "The Dream",
            "releaseDate": "1998-06-01T07:00:00Z",
        }
    ],
}


def _repository() -> Repository:
    """Return a Repository backed by an in-memory SQLite database."""
    return Repository(DatabaseManager(":memory:"))


def _returning(payload: dict[str, Any]) -> Any:
    """Return an http_get_json stand-in that answers every request with payload."""

    def http_get_json(_url: str, _params: dict[str, str]) -> dict[str, Any]:
        return payload

    return http_get_json


class _Response(io.BytesIO):
    """In-memory urlopen response usable as a context manager."""

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


def _raising(error: Exception) -> Any:
    """Return an http_get_json stand-in that raises error on every request."""

    def http_get_json(_url: str, _params: dict[str, str]) -> dict[str, Any]:
        raise error

    return http_get_json


# Candidate ids hold the file id.


def test_musicbrainz_candidate_ids_differ_per_file() -> None:
    adapter = MusicBrainzAdapter(
        _repository(), http_get_json=_returning(MB_SEARCH_PAYLOAD), min_interval_sec=0.0
    )
    first = adapter.search(QUERY, file_id=FIRST_FILE_ID)
    second = adapter.search(QUERY, file_id=SECOND_FILE_ID)
    assert first[0].id != second[0].id
    assert FIRST_FILE_ID in first[0].id
    assert SECOND_FILE_ID in second[0].id


def test_discogs_candidate_ids_differ_per_file() -> None:
    payload = {"results": [{"id": 249504, "title": "Deep Dish - Junk Science", "year": "1998"}]}
    adapter = DiscogsAdapter(
        _repository(),
        user_token=DISCOGS_STUB_KEY,
        http_get_json=_returning(payload),
        min_interval_sec=0.0,
    )
    first = adapter.search(QUERY, file_id=FIRST_FILE_ID)
    second = adapter.search(QUERY, file_id=SECOND_FILE_ID)
    assert first[0].id != second[0].id
    assert first[0].release_details.year == "1998"


def test_itunes_candidate_ids_differ_per_file() -> None:
    adapter = ItunesSearchAdapter(
        _repository(), http_get_json=_returning(ITUNES_PAYLOAD), limiter=RequestLimiter(0.0)
    )
    first = adapter.search(QUERY, file_id=FIRST_FILE_ID)
    second = adapter.search(QUERY, file_id=SECOND_FILE_ID)
    assert first[0].id != second[0].id
    assert first[0].provider_entity_id == "123456789"


# Discogs release normalization.


def test_discogs_release_tracks_keep_distinct_entity_ids_and_drop_year_zero() -> None:
    adapter = DiscogsAdapter(
        _repository(),
        user_token=DISCOGS_STUB_KEY,
        http_get_json=_returning(DISCOGS_RELEASE_PAYLOAD),
        min_interval_sec=0.0,
    )
    candidates = adapter.fetch("249504", file_id=FIRST_FILE_ID)
    positions = [c.release_details.track_number for c in candidates]
    assert positions == ["1", "A1", "3"]
    entity_ids = [c.provider_entity_id for c in candidates]
    assert entity_ids == ["249504:1", "249504:A1", "249504:3"]
    assert len({c.id for c in candidates}) == len(candidates)
    assert all(c.release_details.year is None for c in candidates)


# MusicBrainz release lookup.


def test_musicbrainz_release_fetch_returns_a_release_candidate() -> None:
    adapter = MusicBrainzAdapter(
        _repository(), http_get_json=_returning(MB_RELEASE_PAYLOAD), min_interval_sec=0.0
    )
    candidates = adapter.fetch("rel-9", EntityType.RELEASE, file_id=FIRST_FILE_ID)
    assert len(candidates) == 1
    release = candidates[0]
    assert release.entity_type == EntityType.RELEASE
    assert release.provider_entity_id == "rel-9"
    assert release.artist == "Deep Dish"
    assert release.release_details.label == "Deconstruction"
    assert release.release_details.catalog_number == "DD001"
    assert release.release_details.year == "1998"
    assert release.release_details.track_number is None


def test_musicbrainz_unsupported_entity_type_sends_no_request() -> None:
    requested: list[str] = []

    def record_request(url: str, _params: dict[str, str]) -> dict[str, Any]:
        requested.append(url)
        return {}

    adapter = MusicBrainzAdapter(_repository(), http_get_json=record_request, min_interval_sec=0.0)
    with pytest.raises(ValueError, match="does not support"):
        adapter.fetch("rel-9", cast(EntityType, "label"))
    assert requested == []


def test_musicbrainz_missing_track_number_reads_as_none() -> None:
    adapter = MusicBrainzAdapter(
        _repository(), http_get_json=_returning(MB_SEARCH_PAYLOAD), min_interval_sec=0.0
    )
    candidates = adapter.search(QUERY, file_id=FIRST_FILE_ID)
    assert candidates[0].release_details.track_number is None


# Network failures degrade to an empty result.

NETWORK_ERRORS = [
    urllib.error.URLError("connection refused"),
    TimeoutError("timed out"),
    ValueError("Expecting value"),
]


@pytest.mark.parametrize("error", NETWORK_ERRORS)
def test_musicbrainz_lookup_failure_returns_no_candidates(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = MusicBrainzAdapter(_repository(), http_get_json=_raising(error), min_interval_sec=0.0)
    with caplog.at_level(logging.WARNING, logger=http_json.__name__):
        assert adapter.search(QUERY, file_id=FIRST_FILE_ID) == []
        assert adapter.fetch("rel-9", EntityType.RELEASE, file_id=FIRST_FILE_ID) == []
    assert "MusicBrainz lookup failed" in caplog.text


@pytest.mark.parametrize("error", NETWORK_ERRORS)
def test_discogs_lookup_failure_returns_no_candidates(error: Exception) -> None:
    adapter = DiscogsAdapter(
        _repository(),
        user_token=DISCOGS_STUB_KEY,
        http_get_json=_raising(error),
        min_interval_sec=0.0,
    )
    assert adapter.search(QUERY, file_id=FIRST_FILE_ID) == []
    assert adapter.fetch("249504", file_id=FIRST_FILE_ID) == []


@pytest.mark.parametrize("error", NETWORK_ERRORS)
def test_itunes_lookup_failure_returns_no_candidates(error: Exception) -> None:
    adapter = ItunesSearchAdapter(
        _repository(), http_get_json=_raising(error), limiter=RequestLimiter(0.0)
    )
    assert adapter.search(QUERY, file_id=FIRST_FILE_ID) == []


def test_failed_lookup_is_not_cached() -> None:
    repository = _repository()
    failing = MusicBrainzAdapter(
        repository,
        http_get_json=_raising(urllib.error.URLError("offline")),
        min_interval_sec=0.0,
    )
    assert failing.search(QUERY, file_id=FIRST_FILE_ID) == []
    working = MusicBrainzAdapter(
        repository, http_get_json=_returning(MB_SEARCH_PAYLOAD), min_interval_sec=0.0
    )
    assert working.search(QUERY, file_id=FIRST_FILE_ID) != []


@pytest.mark.parametrize("error", NETWORK_ERRORS)
def test_acoustid_lookup_failure_returns_a_warning(error: Exception) -> None:
    adapter = ChromaprintAcoustIDAdapter(
        _repository(),
        api_key=ACOUSTID_TEST_KEY,
        fpcalc_runner=lambda _path: (240.0, "AQADtEmUJEkS"),
        http_get_json=_raising(error),
    )
    result = adapter.identify(Path("track.wav"), file_id=FIRST_FILE_ID)
    assert result.matches == []
    assert result.raw_fingerprint == "AQADtEmUJEkS"
    assert len(result.warnings) == 1
    assert "AcoustID lookup failed" in result.warnings[0]
    assert ACOUSTID_TEST_KEY not in result.warnings[0]


def test_fpcalc_failure_returns_a_warning() -> None:
    def failing_fpcalc(_path: Path) -> tuple[float, str]:
        raise subprocess.CalledProcessError(2, ["fpcalc"])

    adapter = ChromaprintAcoustIDAdapter(
        _repository(), api_key=ACOUSTID_TEST_KEY, fpcalc_runner=failing_fpcalc
    )
    result = adapter.identify(Path("track.wav"), file_id=FIRST_FILE_ID)
    assert result.matches == []
    assert "fpcalc execution failed" in result.warnings[0]


def test_fpcalc_programming_error_is_not_swallowed() -> None:
    def broken_fpcalc(_path: Path) -> tuple[float, str]:
        raise AttributeError("defect in the runner")

    adapter = ChromaprintAcoustIDAdapter(
        _repository(), api_key=ACOUSTID_TEST_KEY, fpcalc_runner=broken_fpcalc
    )
    with pytest.raises(AttributeError):
        adapter.identify(Path("track.wav"), file_id=FIRST_FILE_ID)


# Shared HTTP helper.


def test_fetch_json_rejects_plain_http_without_a_request() -> None:
    opened: list[Any] = []
    with pytest.raises(ValueError, match="Example URL must use https:// scheme"):
        http_json.fetch_json(
            "http://example.invalid/x", {}, {}, provider="Example", open_url=opened.append
        )
    assert opened == []


def test_fetch_json_reads_a_bounded_body() -> None:
    def open_url(_request: Any, timeout: float) -> _Response:
        assert timeout == http_json.DEFAULT_REQUEST_TIMEOUT_SEC
        return _Response(b'{"ok": true}')

    result = http_json.fetch_json(
        "https://example.invalid/x", {"q": "a b"}, {}, provider="Example", open_url=open_url
    )
    assert result == {"ok": True}


def test_itunes_request_limiter_is_the_shared_limiter() -> None:
    limiter = RequestLimiter()
    assert isinstance(limiter, http_json.RequestLimiter)
    assert limiter.min_interval_sec > 0.0


# Stub adapters report unavailable.


def test_beatport_with_token_reports_unavailable() -> None:
    ready, reason = BeatportStubAdapter(oauth_token=BEATPORT_STUB_KEY).is_available()
    assert ready is False
    assert reason


def test_acrcloud_with_credentials_reports_unavailable_and_warns() -> None:
    adapter = ACRCloudOptInAdapter(
        enabled_by_user=True, access_key=ACRCLOUD_STUB_KEY, access_secret=ACRCLOUD_STUB_VALUE
    )
    ready, reason = adapter.is_available()
    assert ready is False
    result = adapter.identify(Path("track.wav"))
    assert result.warnings == [reason]


# LLM validator pairs the value with the selected candidate.


def test_validator_rejects_a_value_from_another_candidate() -> None:
    candidates = [{"id": "c1", "value": "House"}, {"id": "c2", "value": "Techno"}]
    valid, reason = ClosedWorldValidator().validate_response(
        {"selected_candidate_id": "c1", "proposed_value": "Techno"}, candidates
    )
    assert valid is False
    assert reason


def test_validator_accepts_the_value_of_the_selected_candidate() -> None:
    candidates = [{"id": "c1", "value": "House"}, {"id": "c2", "value": "Techno"}]
    valid, reason = ClosedWorldValidator().validate_response(
        {"selected_candidate_id": "c2", "proposed_value": " techno "}, candidates
    )
    assert (valid, reason) == (True, None)


# Ollama tags endpoint.


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434/api/tags"),
        ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/api/tags"),
        ("http://localhost:8080/v1/", "http://localhost:8080/api/tags"),
    ],
)
def test_ollama_tags_url_follows_the_base_url(base_url: str, expected: str) -> None:
    assert build_tags_url(base_url) == expected
    assert OllamaBootstrapService(base_url=base_url).tags_url == expected


@pytest.mark.parametrize("body", [b"[]", b'"text"', b'{"models": "none"}', b'{"models": [1]}'])
def test_ollama_malformed_tags_response_lists_no_models(
    body: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ollama_module.urllib.request, "urlopen", lambda *_a, **_k: _Response(body))
    assert OllamaBootstrapService().list_installed_models() == []


def test_ollama_lists_model_names(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"models": [{"name": "qwen2.5:3b"}, {"size": 1}]}'
    monkeypatch.setattr(ollama_module.urllib.request, "urlopen", lambda *_a, **_k: _Response(body))
    assert OllamaBootstrapService().list_installed_models() == ["qwen2.5:3b"]


def test_ollama_refuses_a_remote_plain_http_server(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[Any] = []
    monkeypatch.setattr(ollama_module.urllib.request, "urlopen", lambda *a, **_k: opened.append(a))
    service = OllamaBootstrapService(base_url="http://203.0.113.5:11434")
    assert service.list_installed_models() == []
    assert opened == []


# Forget all credentials.


class _PartlyFailingStore(CredentialStore):
    """Memory store whose delete fails for one key."""

    def __init__(self, failing_key: str) -> None:
        super().__init__(use_memory_backend=True)
        self.failing_key = failing_key
        self.attempted: list[str] = []

    def delete_secret(self, key_name: str) -> None:
        self.attempted.append(key_name)
        if key_name == self.failing_key:
            raise CredentialStoreError(f"Failed to delete credential '{key_name}'.")
        super().delete_secret(key_name)


def test_forget_all_credentials_continues_past_a_failed_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_name in ENV_VAR_MAP.values():
        monkeypatch.delenv(env_name, raising=False)
    first_key = next(iter(ENV_VAR_MAP))
    store = _PartlyFailingStore(failing_key=first_key)
    for key_name in ENV_VAR_MAP:
        store.set_secret(key_name, "synthetic-secret")
    assert forget_all_credentials(store) == FORGET_FAILED_EXIT_CODE
    assert store.attempted == list(ENV_VAR_MAP)
    remaining = [k for k in ENV_VAR_MAP if store.get_secret(k)]
    assert remaining == [first_key]


def test_forget_all_credentials_returns_zero_when_every_delete_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_name in ENV_VAR_MAP.values():
        monkeypatch.delenv(env_name, raising=False)
    store = CredentialStore(use_memory_backend=True)
    for key_name in ENV_VAR_MAP:
        store.set_secret(key_name, "synthetic-secret")
    assert forget_all_credentials(store) == 0
    assert [k for k in ENV_VAR_MAP if store.get_secret(k)] == []
