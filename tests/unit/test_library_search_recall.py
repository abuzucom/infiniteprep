"""Exercise saved searches after reopening a library and from the library menu."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.library_query import parse_library_query
from tests.unit.test_library_filters_and_theme_sync import build_window, visible_names


def test_reopened_library_retains_search(tmp_path: Path) -> None:
    library = tmp_path / "search.sqlite"
    first = DatabaseManager(library)
    saved = Repository(first).collections.save_search("Long tracks", "duration:4:00-6:00")
    first.close()
    second = DatabaseManager(library)
    try:
        collections = Repository(second).collections
        assert collections.saved_searches() == [("Long tracks", "duration:4:00-6:00")]
        assert collections.playlist_tracks(saved.id) == []
    finally:
        second.close()


def test_menu_recalls_saved_query(qtbot: QtBot, tmp_path: Path) -> None:
    window, repo, _ = build_window(qtbot, tmp_path, ["a.wav", "b.wav"])
    repo.collections.save_search("Track B", "b.wav")
    window._refresh_search_menu()
    action = next(action for action in window.search_menu.actions() if action.text() == "Track B")
    action.trigger()
    assert window.search_input.text() == "b.wav"
    assert visible_names(window) == ["b.wav"]


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("Daft Punk tracks under four minutes", ["a"]),
        ("tracks released after 2000", ["b"]),
        ("tracks with warnings", ["b"]),
        ("tracks without warnings", ["a"]),
        ("duration:4:00-6:00", ["b"]),
        ("genre:house OR genre:disco AND bpm>130", ["a"]),
    ],
)
def test_phrases_select_expected_tracks(phrase: str, expected: list[str]) -> None:
    db = DatabaseManager(":memory:")
    records = [
        dict(id="a", text="Daft Punk", duration=200, year=1999, genre="house", bpm=120),
        dict(id="b", text="Air", duration=300, year=2001, warnings="clip", genre="disco", bpm=120),
    ]
    try:
        assert parse_library_query(phrase).select_ids(db.connect(), records) == expected
    finally:
        db.close()
