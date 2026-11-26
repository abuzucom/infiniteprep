"""Exercise natural-language search through the library search box."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from infiniteprep.domain.enums import FieldName
from tests.unit.test_library_filters_and_theme_sync import build_window, visible_names


def test_search_combines_artist_and_tempo(qtbot: QtBot, tmp_path: Path) -> None:
    """Both endpoints qualify only when the ordinary artist text also matches."""
    tracks = {
        "slow.wav": ("Daft Punk", "119"),
        "lower.wav": ("Daft Punk", "120"),
        "upper.wav": ("Daft Punk", "128"),
        "fast.wav": ("Daft Punk", "129"),
        "other.wav": ("Justice", "124"),
    }
    window, _, file_ids = build_window(qtbot, tmp_path, list(tracks))
    review = window.review_service
    for name, (artist, bpm) in tracks.items():
        review.set_manual_field_value(file_ids[name], FieldName.ARTIST.value, artist)
        review.set_manual_field_value(file_ids[name], FieldName.BPM.value, bpm)
    window.refresh_file_table()

    window.search_input.setText("Daft Punk")
    assert set(visible_names(window)) == {"slow.wav", "lower.wav", "upper.wav", "fast.wav"}

    window.search_input.setText("Daft Punk tracks between 120 and 128 BPM")
    assert set(visible_names(window)) == {"lower.wav", "upper.wav"}
