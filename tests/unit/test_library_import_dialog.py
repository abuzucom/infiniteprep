"""Tests for the three-step library import dialog (D333)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QListWidget, QTableWidget

from infiniteprep.services.library_import.locations import FormatId
from infiniteprep.services.library_import.locator import LocatedLibrary
from infiniteprep.ui.library_import_dialog import (
    STEP_FIND,
    STEP_MAP,
    STEP_REVIEW,
    LibraryImportDialog,
)
from infiniteprep.ui.library_import_model import (
    EMPTY_CONFLICT_TEXT,
    EMPTY_LIST_TEXT,
    OBSERVED_SUFFIX,
    VERSION_UNKNOWN_TEXT,
    build_rows,
    format_conflict,
    format_timestamp,
)

MARKUP_LABEL = "<b>Traktor</b>"
LIBRARY_PATH = "/music/collection.nml"


def build_library(
    path: str = LIBRARY_PATH,
    version: str | None = "3.5.1",
    observed: bool = False,
) -> LocatedLibrary:
    """Return one located library for the dialog."""
    return LocatedLibrary(
        format_id=FormatId.TRAKTOR,
        file_path=Path(path),
        folder_path=Path(path).parent,
        version=version,
        modified_at=1_700_000_000.0,
        observed=observed,
        citation_url="https://support.native-instruments.com/",
        citation_note="The Root Directory holds collection.nml.",
    )


def open_dialog(qtbot, libraries: list[LocatedLibrary]) -> LibraryImportDialog:
    """Open the dialog for the given libraries."""
    dialog = LibraryImportDialog(libraries, readable_formats=[FormatId.TRAKTOR])
    qtbot.addWidget(dialog)
    return dialog


def list_texts(widget: QListWidget) -> list[str]:
    """Return the text of every row in a list widget."""
    return [widget.item(row).text() for row in range(widget.count())]


def test_dialog_starts_on_find_step(qtbot) -> None:
    """The dialog opens on the step that lists the libraries."""
    dialog = open_dialog(qtbot, [build_library()])

    assert dialog.steps.currentIndex() == STEP_FIND
    assert dialog.next_btn.isHidden() is False
    assert dialog.commit_btn.isHidden() is True
    assert list_texts(dialog.found_list) == ["collection.nml"]


def test_dialog_notes_no_library_found(qtbot) -> None:
    """An empty list shows one fixed line instead of an empty box."""
    dialog = open_dialog(qtbot, [])

    assert list_texts(dialog.found_list) == [EMPTY_LIST_TEXT]


def test_dialog_shows_picked_library_details(qtbot) -> None:
    """Picking a row shows its format, version, and path as plain text."""
    dialog = open_dialog(qtbot, [build_library()])

    dialog.found_list.setCurrentRow(0)

    assert dialog.chosen_library() is not None
    assert "Traktor" in dialog.found_details.text()
    assert "3.5.1" in dialog.found_details.text()


def test_dialog_moves_through_three_steps(qtbot) -> None:
    """Next moves from find to map to review, and back returns to the earlier step."""
    dialog = open_dialog(qtbot, [build_library()])

    dialog.next_btn.click()
    assert dialog.steps.currentIndex() == STEP_MAP
    dialog.next_btn.click()
    assert dialog.steps.currentIndex() == STEP_REVIEW
    dialog.back_btn.click()
    assert dialog.steps.currentIndex() == STEP_MAP


def test_dialog_hides_next_on_review(qtbot) -> None:
    """The review step offers the commit action instead of the next action."""
    dialog = open_dialog(qtbot, [build_library()])

    dialog.next_btn.click()
    dialog.next_btn.click()

    assert dialog.steps.currentIndex() == STEP_REVIEW
    assert dialog.next_btn.isHidden() is True
    assert dialog.commit_btn.isHidden() is False


def test_dialog_keeps_entered_mappings(qtbot) -> None:
    """A mapping row becomes a saved mapping with both prefixes."""
    dialog = open_dialog(qtbot, [build_library()])
    dialog.next_btn.click()
    dialog.add_mapping_btn.click()
    table = dialog.mappings_table
    assert isinstance(table, QTableWidget)
    table.item(0, 0).setText("D:\\Music")
    table.item(0, 1).setText("/Users/user/Music")

    mappings = dialog.saved_mappings()

    assert len(mappings) == 1
    assert mappings[0].source_prefix == "D:\\Music"
    assert mappings[0].target_prefix == "/Users/user/Music"


def test_dialog_keeps_entered_root(qtbot) -> None:
    """The root the DJ entered reaches the caller."""
    dialog = open_dialog(qtbot, [build_library()])
    dialog.next_btn.click()
    dialog.root_edit.setText("/Users/user/Music")

    assert dialog.chosen_root() == "/Users/user/Music"


def test_dialog_empty_root_reports_none(qtbot) -> None:
    """An empty root field reports an empty root."""
    dialog = open_dialog(qtbot, [build_library()])
    dialog.next_btn.click()

    assert dialog.chosen_root() == ""


def test_dialog_keeps_hand_named_file(qtbot, tmp_path: Path) -> None:
    """A file the DJ named becomes a located library the import can read."""
    chosen = tmp_path / "collection.nml"
    chosen.write_text('<HEAD PROGRAM="fake"/>', encoding="utf-8")
    dialog = open_dialog(qtbot, [build_library()])

    with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(chosen), "")):
        dialog.choose_file_btn.click()

    picked = dialog.chosen_library()
    assert picked is not None
    assert picked.file_path == chosen
    assert dialog.found_list.count() == 2


def test_dialog_detects_hand_named_format(qtbot, tmp_path: Path) -> None:
    """A hand-picked file carries the format the registry can pick a reader for."""
    chosen = tmp_path / "collection.nml"
    chosen.write_text('<HEAD PROGRAM="fake"/>', encoding="utf-8")
    dialog = open_dialog(qtbot, [build_library()])

    with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(chosen), "")):
        dialog.choose_file_btn.click()

    picked = dialog.chosen_library()
    assert picked is not None
    assert picked.format_id is FormatId.TRAKTOR


def test_dialog_skips_empty_prefix_mapping(qtbot) -> None:
    """A row with no target prefix is not a mapping."""
    dialog = open_dialog(qtbot, [build_library()])
    dialog.next_btn.click()
    dialog.add_mapping_btn.click()
    dialog.mappings_table.item(0, 0).setText("D:\\Music")

    assert dialog.saved_mappings() == []


def test_dialog_removes_selected_mapping_row(qtbot) -> None:
    """The remove action drops the selected mapping row."""
    dialog = open_dialog(qtbot, [build_library()])
    dialog.next_btn.click()
    dialog.add_mapping_btn.click()
    dialog.mappings_table.setCurrentCell(0, 0)

    dialog.remove_mapping_btn.click()

    assert dialog.mappings_table.rowCount() == 0


def test_dialog_shows_merge_conflicts(qtbot) -> None:
    """The conflict list holds one line per conflict."""
    dialog = open_dialog(qtbot, [build_library()])

    dialog.set_conflicts([format_conflict("title", "New", "Old")])

    assert list_texts(dialog.conflicts_list) == ["title: library has New, app has Old"]


def test_dialog_notes_no_value_conflicts(qtbot) -> None:
    """An empty conflict list shows one fixed line."""
    dialog = open_dialog(qtbot, [build_library()])

    dialog.set_conflicts([])

    assert list_texts(dialog.conflicts_list) == [EMPTY_CONFLICT_TEXT]


def test_dialog_reports_import_request(qtbot) -> None:
    """The commit action reports that the DJ asked for the import."""
    dialog = open_dialog(qtbot, [build_library()])
    reported: list[bool] = []
    dialog.import_requested.connect(lambda: reported.append(True))

    dialog.commit_btn.click()

    assert reported == [True]


def test_details_line_renders_markup_plainly(qtbot) -> None:
    """A format name with markup does not render as markup."""
    library = build_library(path="/music/collection.nml")
    dialog = open_dialog(qtbot, [library])
    dialog.found_list.setCurrentRow(0)

    assert dialog.found_details.textFormat() is Qt.TextFormat.PlainText
    dialog.found_details.setText(MARKUP_LABEL)
    assert dialog.found_details.text() == MARKUP_LABEL


def test_row_model_marks_observed_version() -> None:
    """The row model names the version and marks an observed location."""
    rows = build_rows([build_library(), build_library(observed=True)])

    assert rows[0].version_text == "3.5.1"
    assert rows[1].version_text == "3.5.1" + OBSERVED_SUFFIX
    assert rows[0].format_text == "Traktor"


def test_row_model_names_unknown_version() -> None:
    """A library with no readable version shows a fixed line."""
    rows = build_rows([build_library(version=None)])

    assert rows[0].version_text == VERSION_UNKNOWN_TEXT


def test_timestamp_format_names_unknown_time() -> None:
    """A library with no readable time shows a fixed line."""
    assert format_timestamp(None) == VERSION_UNKNOWN_TEXT
