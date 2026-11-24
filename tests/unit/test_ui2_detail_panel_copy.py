"""Tests for the plain copy of the detail panel and its player (#342 item 12)."""

from __future__ import annotations

from infiniteprep.ui import audio_player as audio_player_module
from infiniteprep.ui import detail_panel as detail_panel_module
from infiniteprep.ui.audio_player import format_analysis_failure, format_playback_failure
from infiniteprep.ui.detail_panel import (
    FIELD_TABLE_HEADERS,
    format_analysis_row,
    format_candidate_row,
    format_header_text,
    format_source_cell,
)


def test_panel_labels_are_plain() -> None:
    """Section, button, and empty texts use plain words."""
    module = detail_panel_module
    assert module.WARNINGS_SECTION_TITLE == "Issues"
    assert module.NO_WARNINGS_TEXT == "No issues."
    assert module.PLAY_SELECTED_LABEL == "Play"
    assert module.REPLACE_TRACK_CONFIRM_LABEL == "Load paused"
    assert module.LOCK_TITLE == "Lock field"
    assert module.NO_SELECTION_TEXT == "Pick a track to see its details."
    assert module.FIELDS_TABLE_NAME == "Changes"
    assert module.CANDIDATES_LIST_NAME == "Possible matches"


def test_field_table_headers_are_plain() -> None:
    """The Changes table names its value columns Now, Suggested, Source, and New value."""
    assert FIELD_TABLE_HEADERS[1:5] == ("Now", "Suggested", "Source", "New value")


def test_header_names_the_file_and_a_save_limit() -> None:
    """The header shows the file name, and says so when tags can't be saved."""
    assert format_header_text("a.wav", write_supported=True) == "a.wav"
    assert format_header_text("a.ogg", write_supported=False) == (
        "a.ogg (can't save tags to this file)"
    )


def test_source_cell_shows_provider_and_match() -> None:
    """The source cell shows the provider and its match as a percentage."""
    assert format_source_cell("acoustid", 0.88) == "acoustid, 88% match"
    assert format_source_cell("acoustid", None) == "acoustid"


def test_candidate_and_analysis_rows_read_plainly() -> None:
    """Candidate rows name the release and its source. Analysis rows say what was detected."""
    assert (
        format_candidate_row(
            provider="discogs",
            artist="A",
            title="T",
            mix_name=None,
            label="X",
            catalog_number="Y",
            match_score=0.91,
        )
        == "A - T (Original), X Y. From discogs, 91% match"
    )
    assert (
        format_candidate_row(
            provider="discogs",
            artist="A",
            title="T",
            mix_name="Dub",
            label=None,
            catalog_number=None,
            match_score=None,
        )
        == "A - T (Dub). From discogs"
    )
    assert format_analysis_row("128", "8A") == "Detected: 128 BPM, key 8A"
    assert format_analysis_row(None, "8A") == "Detected: key 8A"
    assert format_analysis_row(None, None) == "Nothing detected"


def test_player_texts_are_plain() -> None:
    """The player names an empty slot, a missing backend, and failures plainly."""
    module = audio_player_module
    assert module.LOADED_TRACK_NONE_TEXT == "No track loaded"
    assert module.BACKEND_UNAVAILABLE_TEXT == (
        "Audio playback isn't available. Check your audio device, then restart InfinitePrep."
    )
    assert format_playback_failure("decoder missing") == (
        "This track won't play (decoder missing). Check that it plays in another app, "
        "then try again."
    )
    assert format_analysis_failure("file is unreadable") == (
        "Couldn't draw the waveform: file is unreadable"
    )
