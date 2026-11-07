"""Behavioral tests for detail panel refreshes, the full field list, and Other Tags."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QAbstractItemView

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, KeyDisplayNotation
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.detail_panel import (
    COL_FIELD_NAME,
    COL_OTHER_KEY,
    COL_OTHER_VALUE,
    OTHER_TAG_DISPLAY_LIMIT,
    TRUNCATION_MARK,
    DetailPanel,
    format_other_tag_value,
)
from tests.fixtures.audio_factory import create_wav_fixture

TRACK_SECONDS = 2.0
SEEK_SECONDS = 0.75
LONG_VALUE_LENGTH = OTHER_TAG_DISPLAY_LIMIT * 2
BINARY_SAMPLE = b"\x00\x01binary frame body\xff"
ADDED_RAW_TAGS: dict[str, object] = {
    "zeta": "last",
    "Alpha": ["one", "two"],
    "_internal_note": "hidden",
    "mid": 7,
}


@pytest.fixture
def panel_parts(qtbot: Any, tmp_path: Path) -> Any:
    """Import two real WAV files and return the panel, repository, and both file IDs."""
    first = create_wav_fixture(
        tmp_path / "music" / "first.wav",
        duration_sec=TRACK_SECONDS,
        tags={"artist": "Artist A", "title": "Track A"},
        include_traktor_frame=True,
    )
    create_wav_fixture(
        tmp_path / "music" / "second.wav", duration_sec=TRACK_SECONDS, frequencies_hz=(440.0,)
    )
    repository = Repository(DatabaseManager(":memory:"))
    report = ImportService(repository).import_paths([first.parent])
    by_name = {Path(asset.current_path).name: asset.id for asset in report.imported_files}
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)
    return panel, repository, by_name["first.wav"], by_name["second.wav"]


def _start_playback_mid_track(panel: DetailPanel) -> None:
    """Seek into the loaded track and press Play on the silent preview."""
    panel.audio_player.seek_to_seconds(SEEK_SECONDS)
    panel.audio_player.toggle_play_pause()


def _assert_playback_kept(panel: DetailPanel, expected_path: Path) -> None:
    """Assert the player still holds the same file, position, and play state."""
    player = panel.audio_player
    assert player.current_file_path == expected_path
    assert player.current_position_sec == pytest.approx(SEEK_SECONDS)
    assert player.play_btn.text() == "Pause"


def test_key_notation_change_keeps_playback(panel_parts: Any) -> None:
    """A notation change redraws the tables without reloading the playing track."""
    panel, _repository, first_id, _second_id = panel_parts
    panel.load_file_asset(first_id)
    loaded_path = panel.audio_player.current_file_path
    assert loaded_path is not None
    _start_playback_mid_track(panel)

    panel.set_key_notation(KeyDisplayNotation.OPEN_KEY)

    _assert_playback_kept(panel, loaded_path)


def test_field_actions_keep_playback(panel_parts: Any) -> None:
    """Saving a manual edit and toggling a lock refresh the same file without a reload."""
    panel, repository, first_id, _second_id = panel_parts
    panel.load_file_asset(first_id)
    loaded_path = panel.audio_player.current_file_path
    assert loaded_path is not None
    _start_playback_mid_track(panel)

    panel._on_save_manual(first_id, FieldName.GENRE.value, "Techno")
    _assert_playback_kept(panel, loaded_path)
    panel._on_toggle_lock(first_id, FieldName.GENRE.value, locked=False)
    _assert_playback_kept(panel, loaded_path)
    approved = repository.get_approved_fields_map(first_id)[FieldName.GENRE.value]
    assert approved.approved_value == "Techno"


def test_selecting_another_file_loads_it(panel_parts: Any) -> None:
    """A different file replaces the track and starts it from the beginning."""
    panel, repository, first_id, second_id = panel_parts
    panel.load_file_asset(first_id)
    _start_playback_mid_track(panel)

    panel.load_file_asset(second_id)

    second_asset = repository.get_file_by_id(second_id)
    assert second_asset is not None
    assert panel.audio_player.current_file_path == Path(second_asset.current_path)
    assert panel.audio_player.current_position_sec == 0.0


def test_every_field_name_has_a_row(panel_parts: Any) -> None:
    """The fields table lists every FieldName in enum order."""
    panel, _repository, first_id, _second_id = panel_parts
    panel.load_file_asset(first_id)

    table = panel.fields_table
    shown = [table.item(row, COL_FIELD_NAME).text() for row in range(table.rowCount())]
    assert shown == [field.value for field in FieldName]


def _rendered_tooltip(tooltip: str) -> str:
    """Return the text a user sees when Qt renders the tooltip as rich text."""
    document = QTextDocument()
    document.setHtml(tooltip)
    return document.toPlainText()


def _other_tag_cells(panel: DetailPanel) -> list[tuple[str, str, str]]:
    """Return (key, cell text, rendered tooltip text) for each Other Tags row."""
    table = panel.other_tags_table
    cells = []
    for row in range(table.rowCount()):
        value_item = table.item(row, COL_OTHER_VALUE)
        key_text = table.item(row, COL_OTHER_KEY).text()
        cells.append((key_text, value_item.text(), _rendered_tooltip(value_item.toolTip())))
    return cells


def test_other_tags_show_sorted_without_internal_keys(panel_parts: Any) -> None:
    """Other Tags lists the file's custom tags by key and hides app-internal keys (D245)."""
    panel, repository, first_id, _second_id = panel_parts
    asset = repository.get_file_by_id(first_id)
    assert asset is not None
    raw_tags = asset.original_tags.setdefault("raw_custom_tags", {})
    # The PRIV frame from the real import stays alongside the added rows.
    imported_keys = [key for key in raw_tags if not key.startswith("_")]
    assert imported_keys
    raw_tags.update(ADDED_RAW_TAGS)
    repository.upsert_file_asset(asset)

    panel.load_file_asset(first_id)

    cells = _other_tag_cells(panel)
    keys = [key for key, _text, _tip in cells]
    assert keys == sorted([*imported_keys, "zeta", "Alpha", "mid"], key=str.casefold)
    assert "_internal_note" not in keys
    assert "_bits_per_sample" not in keys
    by_key = {key: (text, tip) for key, text, tip in cells}
    assert by_key["Alpha"] == ("one; two", "one; two")
    assert by_key["mid"] == ("7", "7")
    no_edit = QAbstractItemView.EditTrigger.NoEditTriggers
    assert panel.other_tags_table.editTriggers() == no_edit


def test_long_other_tag_value_truncates_with_full_tooltip(panel_parts: Any) -> None:
    """A long value shows cut short in the cell and whole in the tooltip."""
    panel, repository, first_id, _second_id = panel_parts
    asset = repository.get_file_by_id(first_id)
    assert asset is not None
    long_value = "x" * LONG_VALUE_LENGTH
    asset.original_tags["raw_custom_tags"] = {"COMMENT": long_value}
    repository.upsert_file_asset(asset)

    panel.load_file_asset(first_id)

    [(key, text, tooltip)] = _other_tag_cells(panel)
    assert key == "COMMENT"
    assert len(text) == OTHER_TAG_DISPLAY_LIMIT
    assert text.endswith(TRUNCATION_MARK)
    assert tooltip == long_value


def test_binary_other_tag_value_shows_size_and_digest() -> None:
    """Bytes show as their size and SHA-256 rather than raw content."""
    digest = hashlib.sha256(BINARY_SAMPLE).hexdigest()
    expected = f"<{len(BINARY_SAMPLE)} bytes, sha256 {digest}>"

    assert format_other_tag_value(BINARY_SAMPLE) == expected
    assert format_other_tag_value([BINARY_SAMPLE, "text"]) == f"{expected}; text"
