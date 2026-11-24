"""Tests for the formats the app can read and the link refused at the open."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.services.library_import.filesystem import open_inside_root
from infiniteprep.services.library_import.locations import FormatId
from infiniteprep.services.library_import.locator import LocatedLibrary
from infiniteprep.ui.library_import_dialog import LibraryImportDialog
from infiniteprep.ui.library_import_model import EMPTY_LIST_TEXT

OUTSIDE_BYTES = b"the bytes outside the root"


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


def test_the_dialog_offers_nothing_while_the_app_holds_no_reader(qtbot) -> None:
    """A format with no reader stays out of the list of the production dialog."""
    dialog = LibraryImportDialog([build_library("/music/collection.nml", FormatId.TRAKTOR)])
    qtbot.addWidget(dialog)

    assert dialog.found_list.count() == 1
    assert dialog.found_list.item(0).text() == EMPTY_LIST_TEXT
    dialog.found_list.setCurrentRow(0)
    assert dialog.chosen_library() is None


def test_the_open_refuses_a_file_that_a_link_replaced(tmp_path: Path) -> None:
    """A path that names a link never opens the file the link points at."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(OUTSIDE_BYTES)
    link = root / "track.wav"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("the platform does not allow a symbolic link")

    assert open_inside_root(link, root) is None


def test_the_open_reads_a_real_file_inside_the_root(tmp_path: Path) -> None:
    """A real file inside the root opens and keeps its handle."""
    root = tmp_path / "root"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(OUTSIDE_BYTES)

    opened = open_inside_root(track, root)

    assert opened is not None
    with opened:
        assert opened.handle.read() == OUTSIDE_BYTES
