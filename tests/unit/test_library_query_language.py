"""Exercise query compilation against effective metadata and the library UI."""

import sqlite3

import pytest

from infiniteprep.domain.library_query import parse_library_query


@pytest.mark.parametrize(
    "source",
    [
        'artist:"daft" AND title="One More Time"',
        "mix:club AND label:virgin",
        "catalog_number=V1 AND genre:house",
        "year>=2000 AND year<2002",
        "bpm:120-128 AND duration<=4:00",
        "bpm>120 AND key:8A",
        "format=FLAC AND NOT status:rejected",
        "warnings:clipped",
        'key:"A minor"',
        "house or disco tracks shorter than four minutes",
        "tracks by Daft Punk",
        "tracks in the key of 8A",
        "(genre:house OR genre:disco) AND NOT bpm>=128",
    ],
)
def test_query_matches_metadata(source):
    row = dict(
        id="one",
        text="daft punk house",
        artist="daft punk",
        title="one more time",
        mix="club",
        label="virgin",
        catalog_number="v1",
        genre="house",
        year=2001,
        bpm=124,
        key="8a",
        duration=239,
        format="flac",
        status="approved",
        warnings="clipped",
    )
    with sqlite3.connect(":memory:") as conn:
        query = parse_library_query(source)
        assert query.select_ids(conn, [row, {"id": "other"}]) == ["one"]


@pytest.mark.parametrize(
    "source",
    [
        "artist:",
        "bpm > nope",
        "key:unknown",
        "(genre:house",
        "bpm:128-120",
        "NOT " * 80 + "artist:x",
        "x" * 2049,
        'artist:"unfinished',
        "genre:x AND",
    ],
)
def test_query_rejects_invalid_syntax(source):
    with pytest.raises(ValueError):
        parse_library_query(source)


def test_search_treats_quotes_literally():
    value = """x' OR 1=1 --"""
    with sqlite3.connect(":memory:") as conn:
        query = parse_library_query(f'artist="{value}"')
        assert query.select_ids(conn, [{"id": "a", "artist": value}, {"id": "b"}]) == ["a"]
