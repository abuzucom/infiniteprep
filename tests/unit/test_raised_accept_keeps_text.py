"""Behavioral test for an Accept that raises while the row holds unsaved text."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QLineEdit

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.detail_panel import COLUMN_APPROVED_EDIT, DISPLAY_FIELDS, DetailPanel
from tests.fixtures.audio_factory import create_wav_fixture

ARTIST_ROW: int = DISPLAY_FIELDS.index(FieldName.ARTIST)
TYPED_ARTIST: str = "Typed artist"


class AcceptFailedError(RuntimeError):
    """A storage failure raised by the review service during an accept."""


def artist_text(panel: DetailPanel) -> str:
    """Return the text in the artist row edit box."""
    widget = panel.fields_table.cellWidget(ARTIST_ROW, COLUMN_APPROVED_EDIT)
    assert isinstance(widget, QLineEdit)
    return widget.text()


def test_raised_accept_keeps_typed_text(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accept that raises leaves no marker behind. The next refresh keeps the typed text."""
    repository = Repository(DatabaseManager(":memory:"))
    track = create_wav_fixture(tmp_path / "crate" / "a.wav")
    report = ImportService(repository, MetadataTagAdapter()).import_paths([track])
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)
    file_id = report.imported_files[0].id
    panel.load_file_asset(file_id)
    edit = panel.fields_table.cellWidget(ARTIST_ROW, COLUMN_APPROVED_EDIT)
    edit.setText(TYPED_ARTIST)

    def fail_accept(_proposal_id: str) -> bool:
        raise AcceptFailedError("database locked")

    monkeypatch.setattr(panel.review_service, "accept_proposal", fail_accept)
    with pytest.raises(AcceptFailedError):
        panel._accept_row_proposal(FieldName.ARTIST.value, "failing-proposal")

    assert panel._acted_field is None
    panel.load_file_asset(file_id)
    assert artist_text(panel) == TYPED_ARTIST
