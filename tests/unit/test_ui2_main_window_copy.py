"""Tests for the plain copy of the main window, its table, and its sidebar (#342 item 12)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.library_sidebar import FILTER_LABELS
from infiniteprep.ui.main_window import (
    TABLE_COLUMNS,
    MainWindow,
    format_analyze_label,
    format_approved_count,
    format_warning_summary,
)
from infiniteprep.ui.workflow import LibraryFilter


def test_column_headers_are_plain() -> None:
    """The library columns use short names. Label / Cat# keeps both parts."""
    assert TABLE_COLUMNS == (
        "Filename",
        "Format",
        "Duration",
        "Artist",
        "Title",
        "Mix",
        "Label / Cat#",
        "BPM",
        "Key",
        "Status",
        "Issues",
    )


def test_issue_cell_counts_issues() -> None:
    """The Issues cell counts issues, with the noun that fits the count."""
    assert format_warning_summary([]) == ""
    assert format_warning_summary(["clipping"]) == "1 issue"
    assert format_warning_summary(["a", "b", "c"]) == "3 issues"


def test_sidebar_filters_are_plain() -> None:
    """The sidebar names its filters in plain words."""
    assert FILTER_LABELS == {
        LibraryFilter.ALL: "All tracks",
        LibraryFilter.NEEDS_REVIEW: "Needs review",
        LibraryFilter.HAS_WARNINGS: "With issues",
        LibraryFilter.READY_TO_WRITE: "Ready to save",
    }


def test_action_titles_and_messages_are_plain() -> None:
    """Dialog titles, the empty message, and the close prompt use plain copy."""
    module = main_window_module
    assert module.ANALYZE_TITLE == "Analyze"
    assert module.BULK_APPROVE_TITLE == "Approve all matches"
    assert module.WRITE_TITLE == "Review changes"
    assert module.TRANSCODE_TITLE == "Transcode"
    assert module.EXPORT_TITLE == "Export playlist"
    assert module.BATCH_RESULT_TITLE == "Results"
    assert module.IMPORT_FAILED_TITLE == "Import failed"
    assert module.CLOSE_PROMPT_TITLE == "Still working"
    assert module.KEEP_WORKING_LABEL == "Keep working"
    assert module.QUIT_NOW_LABEL == "Quit now"
    assert module.CHOOSE_FOLDER_TITLE == "Choose a music folder"
    assert module.PLAYLIST_FILE_FILTER == "Playlist (*.m3u8)"
    assert module.KEY_NOTATION_LABEL == "&Key format:"
    assert module.NO_VISIBLE_FILES_TEXT == "Nothing here yet. Add a folder or clear your filters."
    assert module.CLOSE_PROMPT_TEXT == (
        "You still have work running. If you quit now, it stops after the current file "
        "and anything waiting is dropped. A cancelled import saves nothing."
    )


def test_counts_read_with_the_right_noun() -> None:
    """The approve result and the Analyze batch name fit their counts."""
    assert format_approved_count(1) == "Approved 1 field."
    assert format_approved_count(3) == "Approved 3 fields."
    assert format_analyze_label(1) == "Analyze 1 file"
    assert format_analyze_label(3) == "Analyze 3 files"


def test_window_title_is_the_app_name(qtbot: Any, tmp_path: Path) -> None:
    """The main window title is the app name alone."""
    repository = Repository(DatabaseManager(tmp_path / "copy.sqlite3"))
    window = MainWindow(
        repository, ImportService(repository, MetadataTagAdapter()), ReviewService(repository)
    )
    qtbot.addWidget(window)

    assert window.windowTitle() == "InfinitePrep"
