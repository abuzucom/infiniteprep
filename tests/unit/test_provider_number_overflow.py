"""Tests that an integer past the float range reads as an absent provider number."""

from __future__ import annotations

from typing import Any

from infiniteprep.adapters.catalog.musicbrainz import MusicBrainzAdapter
from infiniteprep.adapters.provider_fields import provider_number
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.ports import MetadataSearchQuery

# A JSON integer of this size parses in Python but has no float form.
HUGE_INTEGER = 10**400


def test_provider_number_returns_none_for_an_integer_past_the_float_range() -> None:
    """float() raises OverflowError for this integer. The check returns None instead."""
    assert provider_number(HUGE_INTEGER) is None


def test_musicbrainz_lookup_survives_a_huge_length() -> None:
    """A recording length past the float range reads as no duration, not a failed lookup."""
    payload: dict[str, Any] = {
        "recordings": [{"id": "rec-1", "title": "The Dream", "length": HUGE_INTEGER}]
    }
    adapter = MusicBrainzAdapter(
        Repository(DatabaseManager(":memory:")),
        http_get_json=lambda _url, _params: payload,
        min_interval_sec=0.0,
    )

    candidates = adapter.search(MetadataSearchQuery(title="The Dream"))

    assert len(candidates) == 1
    assert candidates[0].duration is None
