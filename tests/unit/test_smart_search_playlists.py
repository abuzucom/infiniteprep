"""Verify live membership and recovery from an invalid search in the library window."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from infiniteprep.domain.enums import FieldName
from tests.unit.test_library_filters_and_theme_sync import build_window, visible_names


def test_saved_search_tracks_metadata(qtbot: QtBot, tmp_path: Path) -> None:
    window, repo, ids = build_window(qtbot, tmp_path, ["a.wav", "b.wav"])
    collections = repo.collections
    saved = collections.save_search("Club", "bpm >= 125")
    assert saved in collections.list_collections()
    assert collections.playlist_tracks(saved.id) == []
    window.review_service.set_manual_field_value(ids["a.wav"], FieldName.BPM.value, "126")
    assert collections.playlist_tracks(saved.id) == [ids["a.wav"]]
    with pytest.raises(ValueError):
        collections.set_playlist_tracks(saved.id, [ids["b.wav"]])
    assert collections.saved_searches() == [("Club", "bpm >= 125")]


def test_invalid_query_preserves_rows(qtbot: QtBot, tmp_path: Path) -> None:
    window, _, _ = build_window(qtbot, tmp_path, ["a.wav", "b.wav"])
    window.search_input.setText("a.wav")
    window.search_input.setText("bpm >")
    assert visible_names(window) == ["a.wav"]
    assert window.search_error.text()
    window.search_input.setText("b.wav")
    assert visible_names(window) == ["b.wav"]
    assert not window.search_error.text()
