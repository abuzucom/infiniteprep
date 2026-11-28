"""Search hint helpers of the query compiler and the saved search edits."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from infiniteprep.db.collections import SavedSearch
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.library_query import plain_search_term, query_fields


@pytest.fixture
def repository() -> Iterator[Repository]:
    """Open an empty in-memory library."""
    database = DatabaseManager(":memory:")
    yield Repository(database)
    database.close()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Daft Punk", "daft punk"),
        ('"b.wav"', "b.wav"),
        ("bpm > 120", None),
        ("tracks with warnings", None),
        ('"open', None),
    ],
)
def test_plain_search_term_values(source: str, expected: str | None) -> None:
    """A plain search returns its folded term. A structured or invalid search returns None."""
    assert plain_search_term(source) == expected


def test_query_fields_named_fields() -> None:
    """Each named field comes back under its canonical name."""
    assert query_fields("genre:house OR year>2000") == {"genre", "year"}
    assert query_fields("review_status:proposed") == {"status"}
    assert query_fields("daft punk") == frozenset()


def test_query_fields_spoken_genre() -> None:
    """A spoken genre list searches the keyword field."""
    assert "keyword" in query_fields("house tracks under 4 minutes")


def test_query_fields_invalid_source() -> None:
    """An invalid search names no fields."""
    assert query_fields('genre:"open') == frozenset()


def test_saved_search_entries_list(repository: Repository) -> None:
    """Each saved search lists its ID, name, and query."""
    saved = repository.collections.save_search("Club", "bpm >= 125")

    assert repository.collections.saved_search_entries() == [
        SavedSearch(saved.id, "Club", "bpm >= 125")
    ]


def test_saved_search_rename_keeps_query(repository: Repository) -> None:
    """A rename changes the name and keeps the query."""
    collections = repository.collections
    saved = collections.save_search("Club", "bpm >= 125")

    collections.rename_saved_search(saved.id, "Peak time")

    assert collections.saved_searches() == [("Peak time", "bpm >= 125")]


def test_saved_search_rename_duplicate_fails(repository: Repository) -> None:
    """A rename to a name already in use fails and keeps both searches."""
    collections = repository.collections
    collections.save_search("Club", "bpm >= 125")
    warmup = collections.save_search("Warmup", "bpm < 110")

    with pytest.raises(ValueError, match="different name"):
        collections.rename_saved_search(warmup.id, "club")

    assert collections.saved_searches() == [("Club", "bpm >= 125"), ("Warmup", "bpm < 110")]


def test_saved_search_delete_removes_entry(repository: Repository) -> None:
    """A delete removes the saved search."""
    collections = repository.collections
    saved = collections.save_search("Club", "bpm >= 125")

    collections.delete_saved_search(saved.id)

    assert collections.saved_searches() == []


def test_saved_search_delete_spares_playlists(repository: Repository) -> None:
    """A delete refuses an ordinary playlist and leaves it in place."""
    collections = repository.collections
    playlist = collections.create_playlist("Set")

    with pytest.raises(ValueError, match="saved search"):
        collections.delete_saved_search(playlist.id)
    with pytest.raises(ValueError, match="saved search"):
        collections.rename_saved_search(playlist.id, "Other")

    assert [collection.id for collection in collections.list_collections()] == [playlist.id]
