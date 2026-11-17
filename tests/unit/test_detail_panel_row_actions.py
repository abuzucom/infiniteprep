"""Behavioral tests for row names, focus, and unsaved text in the detail panel field rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QWidget

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.detail_panel import (
    COL_ACTIONS,
    COL_APPROVED_EDIT,
    DISPLAY_FIELDS,
    DetailPanel,
)
from tests.fixtures.audio_factory import create_wav_fixture

ARTIST_ROW: int = DISPLAY_FIELDS.index(FieldName.ARTIST)
TITLE_ROW: int = DISPLAY_FIELDS.index(FieldName.TITLE)
TYPED_TITLE: str = "Typed title"


@pytest.fixture
def panel(qtbot: Any, tmp_path: Path) -> DetailPanel:
    """Import one tagged WAV and show its rows in an active detail panel."""
    repository = Repository(DatabaseManager(":memory:"))
    track = create_wav_fixture(
        tmp_path / "crate" / "a.wav", tags={"artist": "Artist A", "title": "Track A"}
    )
    report = ImportService(repository, MetadataTagAdapter()).import_paths([track])
    detail = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(detail)
    detail.show()
    qtbot.waitExposed(detail)
    detail.activateWindow()
    qtbot.waitUntil(detail.isActiveWindow)
    detail.load_file_asset(report.imported_files[0].id)
    return detail


def edit_box(detail: DetailPanel, row: int) -> QLineEdit:
    """Return the edit box of one field row."""
    widget = detail.fields_table.cellWidget(row, COL_APPROVED_EDIT)
    assert isinstance(widget, QLineEdit)
    return widget


def row_button(detail: DetailPanel, row: int, text: str) -> QPushButton:
    """Return the button with the given text in one field row."""
    actions = detail.fields_table.cellWidget(row, COL_ACTIONS)
    matches = [button for button in actions.findChildren(QPushButton) if button.text() == text]
    assert len(matches) == 1
    return matches[0]


def spoken_name(widget: QWidget) -> str:
    """Return the name a screen reader announces for a widget."""
    interface = QAccessible.queryAccessibleInterface(widget)
    return interface.text(QAccessible.Text.Name) if interface is not None else ""


def test_every_row_control_has_a_distinct_name(panel: DetailPanel) -> None:
    """Each row's edit box and buttons name their field, so no two controls sound alike."""
    names: list[str] = []
    for row in range(panel.fields_table.rowCount()):
        actions = panel.fields_table.cellWidget(row, COL_ACTIONS)
        controls = [edit_box(panel, row), *actions.findChildren(QPushButton)]
        names.extend(spoken_name(control) for control in controls)

    assert all(name.strip() for name in names)
    assert len(set(names)) == len(names)


def test_space_on_save_keeps_focus_on_the_same_save_button(qtbot: Any, panel: DetailPanel) -> None:
    """After Save rebuilds the rows, focus sits on the new Save button of the same row."""
    save = row_button(panel, ARTIST_ROW, "Save")
    save.setFocus()
    qtbot.waitUntil(save.hasFocus)

    qtbot.keyClick(save, Qt.Key.Key_Space)

    focused = QApplication.focusWidget()
    assert focused is row_button(panel, ARTIST_ROW, "Save")


def test_lock_in_one_row_keeps_unsaved_text_in_another(panel: DetailPanel) -> None:
    """Lock on the artist row leaves text typed in the title row in place."""
    edit_box(panel, TITLE_ROW).setText(TYPED_TITLE)

    row_button(panel, ARTIST_ROW, "Lock").click()

    assert row_button(panel, ARTIST_ROW, "Unlock") is not None
    assert edit_box(panel, TITLE_ROW).text() == TYPED_TITLE


def test_save_shows_the_saved_value_in_its_own_row(panel: DetailPanel) -> None:
    """Save shows the trimmed saved value in its row, not the text as typed."""
    edit_box(panel, ARTIST_ROW).setText("  New Artist  ")

    row_button(panel, ARTIST_ROW, "Save").click()

    assert edit_box(panel, ARTIST_ROW).text() == "New Artist"
