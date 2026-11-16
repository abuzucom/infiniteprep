"""Behavioral tests for the iTunes Search genre and release-date fallback (D404)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.catalog.itunes import (
    ITUNES_SEARCH_URL,
    ItunesSearchAdapter,
    RequestLimiter,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import AudioFormat, EntityType
from infiniteprep.domain.models import CandidateMatch, FileAsset, ReleaseDetails
from infiniteprep.domain.ports import MetadataSearchQuery
from infiniteprep.services.reconciliation import ReconciliationService

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "itunes_search"
FILE_ID = "file-1"
RATE_LIMIT_INTERVAL_SEC = 0.2
# The Windows scheduler returns from a sleep early by up to one timer tick.
RATE_LIMIT_TOLERANCE_SEC = 0.02


def _recorded(name: str) -> dict[str, Any]:
    """Return the recorded iTunes response stored under the fixture directory."""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _repository() -> Repository:
    """Return a Repository on a fresh in-memory database."""
    return Repository(DatabaseManager(":memory:"))


def _query() -> MetadataSearchQuery:
    """Return the search query for the recorded track."""
    return MetadataSearchQuery(artist="Deep Dish", title="The Dream")


def _asset() -> FileAsset:
    """Return the library row the proposals attach to."""
    return FileAsset(
        id=FILE_ID,
        current_path="/music/file-1.mp3",
        original_path="/music/file-1.mp3",
        file_size=1024,
        content_hash="a" * 64,
        format=AudioFormat.MP3,
        duration=480.0,
        original_tags={"standard_tags": {"artist": "Deep Dish", "title": "The Dream"}},
        import_batch_id="batch-1",
        source_group_id=None,
    )


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


def test_search_returns_the_matching_row_with_a_genre_and_a_date() -> None:
    """A matching row supplies the coarse genre and the release year."""
    adapter = _adapter(_repository(), _recorded("match_with_genre_and_date"))

    matches = adapter.search(_query(), file_id=FILE_ID)

    assert len(matches) == 1
    candidate = matches[0]
    assert candidate.provider == "itunes"
    assert candidate.provider_entity_id == "123456789"
    assert candidate.entity_type == EntityType.RELEASE
    assert candidate.release_details.genre == "Dance"
    assert candidate.release_details.year == "1994"
    assert candidate.release_details.release_title == "The Dream"


def test_search_without_a_match_returns_no_candidate() -> None:
    """An empty response produces no candidate."""
    adapter = _adapter(_repository(), _recorded("no_match"))

    assert adapter.search(_query(), file_id=FILE_ID) == []


def test_the_earliest_release_date_wins_among_tied_rows() -> None:
    """Two collections for one track keep the earliest release date."""
    adapter = _adapter(_repository(), _recorded("two_collections"))

    matches = adapter.search(_query(), file_id=FILE_ID)

    assert len(matches) == 1
    assert matches[0].provider_entity_id == "777888999"
    assert matches[0].release_details.year == "1994"


def test_the_response_artwork_is_ignored() -> None:
    """No artwork field from the response reaches the candidate."""
    adapter = _adapter(_repository(), _recorded("artwork_only"))

    candidate = adapter.search(_query(), file_id=FILE_ID)[0]

    artwork_url = _recorded("artwork_only")["results"][0]["artworkUrl100"]
    assert artwork_url not in json.dumps(candidate.release_details.to_dict())
    assert artwork_url not in json.dumps(candidate.match_evidence)
    assert "artwork" not in json.dumps(candidate.match_evidence).lower()


def test_the_response_is_cached_in_the_shared_sqlite_cache() -> None:
    """A second identical search reads the cached response instead of the network."""
    repository = _repository()
    calls: list[tuple[str, dict[str, str]]] = []
    adapter = _adapter(repository, _recorded("match_with_genre_and_date"), calls)

    first = adapter.search(_query(), file_id=FILE_ID)
    second = adapter.search(_query(), file_id=FILE_ID)

    assert len(calls) == 1
    assert first[0].provider_entity_id == second[0].provider_entity_id


def test_the_rate_limit_holds_the_next_request_back() -> None:
    """Two calls in a row wait the minimum interval between them."""
    adapter = _adapter(
        _repository(),
        _recorded("match_with_genre_and_date"),
        limiter=RequestLimiter(RATE_LIMIT_INTERVAL_SEC),
    )

    started = time.monotonic()
    adapter.search(_query(), file_id=FILE_ID)
    adapter.search(MetadataSearchQuery(artist="Deep Dish", title="Flashdance"), file_id=FILE_ID)
    elapsed = time.monotonic() - started

    assert elapsed >= RATE_LIMIT_INTERVAL_SEC - RATE_LIMIT_TOLERANCE_SEC


def test_an_unrelated_row_supplies_no_candidate() -> None:
    """A row that matches neither the artist nor the title supplies no value."""
    adapter = _adapter(_repository(), _recorded("unrelated_row"))

    assert adapter.search(_query(), file_id=FILE_ID) == []


def test_a_malformed_duration_leaves_the_candidate_without_a_duration() -> None:
    """A row with a malformed duration still supplies the genre and the year."""
    adapter = _adapter(_repository(), _recorded("malformed_duration"))

    matches = adapter.search(_query(), file_id=FILE_ID)

    assert len(matches) == 1
    assert matches[0].duration is None
    assert matches[0].release_details.genre == "Dance"


def test_the_cache_key_names_the_provider_and_the_endpoint() -> None:
    """The cache key holds the provider and the endpoint, so no other source shares it."""
    adapter = _adapter(_repository(), _recorded("match_with_genre_and_date"))

    key = adapter._cache_key({"term": "Deep Dish The Dream"})

    assert key.startswith("itunes:search:")
    assert "itunes.apple.com" not in key


def test_two_threads_send_one_lookup_for_the_same_track() -> None:
    """Two threads that miss the cache together send one request between them."""
    repository = _repository()
    limiter = RequestLimiter(0.0)
    calls: list[str] = []
    lock = threading.Lock()

    def fake_get(url: str, _params: dict[str, str]) -> dict[str, Any]:
        with lock:
            calls.append(url)
        time.sleep(0.05)
        return _recorded("match_with_genre_and_date")

    adapter = ItunesSearchAdapter(repository=repository, http_get_json=fake_get, limiter=limiter)
    results: list[int] = []

    def run_search() -> None:
        matches = adapter.search(
            MetadataSearchQuery(artist="Deep Dish", title="The Dream"), file_id=FILE_ID
        )
        results.append(len(matches))

    threads = [threading.Thread(target=run_search) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(calls) == 1
    assert results == [1, 1]


def test_the_request_carries_the_shared_user_agent_and_the_search_terms() -> None:
    """The request names the search endpoint and sends the artist and the title."""
    calls: list[tuple[str, dict[str, str]]] = []
    adapter = _adapter(_repository(), _recorded("match_with_genre_and_date"), calls)

    adapter.search(_query(), file_id=FILE_ID)

    [(url, params)] = calls
    assert url == ITUNES_SEARCH_URL
    assert params["term"] == "Deep Dish The Dream"
    assert params["media"] == "music"
    assert params["entity"] == "song"
    assert params["country"] == "US"


def test_each_proposal_records_itunes_search_as_its_source() -> None:
    """A proposal from the adapter names iTunes Search as the provider."""
    repository = _repository()
    repository.upsert_file_asset(_asset())
    adapter = _adapter(repository, _recorded("match_with_genre_and_date"))

    for candidate in adapter.search(_query(), file_id=FILE_ID):
        repository.upsert_candidate_match(candidate)
    proposals = ReconciliationService(repository).reconcile_file(FILE_ID)

    genre_proposals = [item for item in proposals if item.field_name == "genre"]
    year_proposals = [item for item in proposals if item.field_name == "year"]
    assert [item.provider for item in genre_proposals] == ["itunes"]
    assert [item.provider for item in year_proposals] == ["itunes"]
    assert genre_proposals[0].provider_entity_id == "123456789"
    assert genre_proposals[0].proposed_value == "Dance"


def test_a_field_another_source_supplied_gets_no_itunes_proposal() -> None:
    """A higher ranked source keeps the field and the iTunes proposal never appears."""
    repository = _repository()
    repository.upsert_file_asset(_asset())
    discogs_candidate = CandidateMatch(
        id="discogs-rel-1",
        file_id=FILE_ID,
        provider="discogs",
        provider_entity_id="1",
        entity_type=EntityType.RELEASE,
        artist="Deep Dish",
        title="The Dream",
        release_details=ReleaseDetails(genre="Electronic", year="1995"),
        match_evidence={"discogs_id": "1"},
        match_score=0.9,
    )
    repository.upsert_candidate_match(discogs_candidate)
    adapter = _adapter(repository, _recorded("match_with_genre_and_date"))
    for candidate in adapter.search(_query(), file_id=FILE_ID):
        repository.upsert_candidate_match(candidate)

    proposals = ReconciliationService(repository).reconcile_file(FILE_ID)

    genre_proposals = [item for item in proposals if item.field_name == "genre"]
    assert [item.provider for item in genre_proposals] == ["discogs"]
    assert not [item for item in proposals if item.provider == "itunes"]


@pytest.mark.parametrize("fixture_name", ["no_match", "artwork_only"])
def test_no_network_call_reaches_the_response_without_a_search_term(
    fixture_name: str,
) -> None:
    """A query without an artist or a title never reaches the network."""
    calls: list[tuple[str, dict[str, str]]] = []
    adapter = _adapter(_repository(), _recorded(fixture_name), calls)

    assert adapter.search(MetadataSearchQuery(), file_id=FILE_ID) == []
    assert calls == []
