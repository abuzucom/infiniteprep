"""Tests for the formats the import dialog offers and the missing-reader error."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from PySide6.QtWidgets import QFileDialog

from infiniteprep.services.library_import.importer import (
    ImporterRegistry,
    NoImporterError,
    import_library,
)
from infiniteprep.services.library_import.locations import FormatId
from infiniteprep.services.library_import.locator import LocatedLibrary
from infiniteprep.ui.library_import_dialog import LibraryImportDialog
from infiniteprep.ui.library_import_model import EMPTY_LIST_TEXT


def build_library(path: str, format_id: FormatId) -> LocatedLibrary:
    """Return one located library for the dialog."""
    return LocatedLibrary(
        format_id=format_id,
        file_path=Path(path),
        folder_path=Path(path).parent,
        version="1.0",
        modified_at=1_700_000_000.0,
        observed=False,
        citation_url="https://support.native-instruments.com/",
        citation_note="The Root Directory holds collection.nml.",
    )


def list_texts(dialog: LibraryImportDialog) -> list[str]:
    """Return the text of every row in the list of the dialog."""
    return [dialog.found_list.item(row).text() for row in range(dialog.found_list.count())]


def test_dialog_hides_unreadable_library(qtbot) -> None:
    """A library whose format has no reader stays out of the list."""
    dialog = LibraryImportDialog(
        [build_library("/music/collection.nml", FormatId.TRAKTOR)],
        readable_formats=[],
    )
    qtbot.addWidget(dialog)

    assert list_texts(dialog) == [EMPTY_LIST_TEXT]


def test_dialog_shows_readable_library(qtbot) -> None:
    """A library whose format has a reader appears in the list."""
    dialog = LibraryImportDialog(
        [build_library("/music/collection.nml", FormatId.TRAKTOR)],
        readable_formats=[FormatId.TRAKTOR],
    )
    qtbot.addWidget(dialog)

    assert list_texts(dialog) == ["collection.nml"]


def test_dialog_keeps_readable_picked_file(qtbot, tmp_path: Path) -> None:
    """A file the DJ names joins the list when the app can read its format."""
    chosen = tmp_path / "collection.nml"
    chosen.write_text('<HEAD PROGRAM="fake"/>', encoding="utf-8")
    dialog = LibraryImportDialog(
        [build_library("/music/collection.nml", FormatId.TRAKTOR)],
        readable_formats=[FormatId.TRAKTOR],
    )
    qtbot.addWidget(dialog)

    with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(chosen), "")):
        dialog.choose_file_btn.click()

    assert list_texts(dialog) == ["collection.nml", "collection.nml"]


def test_missing_reader_error_names_issue(
    tmp_path: Path,
) -> None:
    """The error names the issue that brings the reader of the format."""
    registry = ImporterRegistry()
    source = tmp_path / "collection.nml"

    with pytest.raises(NoImporterError, match="#145"):
        import_library(source, registry, FormatId.TRAKTOR)


def test_reader_error_names_rekordbox_issues(tmp_path: Path) -> None:
    """The error names both issues that bring a rekordbox reader."""
    registry = ImporterRegistry()
    source = tmp_path / "export.pdb"

    with pytest.raises(NoImporterError) as raised:
        import_library(source, registry, FormatId.REKORDBOX)

    message = str(raised.value)
    assert "#144" in message
    assert "#334" in message
