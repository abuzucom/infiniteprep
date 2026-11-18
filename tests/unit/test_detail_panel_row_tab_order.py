"""Behavioral test for the Tab order through one field row of the detail panel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.detail_panel import COLUMN_ACTIONS, COLUMN_APPROVED_EDIT, DetailPanel
from tests.fixtures.audio_factory import create_wav_fixture

# A file without proposals shows no Accept button, so the row holds these two.
EXPECTED_BUTTON_ORDER: list[str] = ["Save", "Lock"]


def test_tab_visits_every_row_button_in_order(qtbot: Any, tmp_path: Path) -> None:
    """Tab from a field edit box visits Save, then Lock, inside the same row."""
    repository = Repository(DatabaseManager(":memory:"))
    track = create_wav_fixture(tmp_path / "crate" / "a.wav")
    report = ImportService(repository, MetadataTagAdapter()).import_paths([track])
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitExposed(panel)
    panel.activateWindow()
    qtbot.waitUntil(panel.isActiveWindow)
    panel.load_file_asset(report.imported_files[0].id)
    edit_box = panel.fields_table.cellWidget(0, COLUMN_APPROVED_EDIT)
    action_cell = panel.fields_table.cellWidget(0, COLUMN_ACTIONS)
    edit_box.setFocus()
    qtbot.waitUntil(edit_box.hasFocus)

    visited: list[str] = []
    for _press in EXPECTED_BUTTON_ORDER:
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        focused = QApplication.focusWidget()
        assert isinstance(focused, QPushButton)
        assert action_cell.isAncestorOf(focused)
        visited.append(focused.text())

    assert visited == EXPECTED_BUTTON_ORDER
