"""Behavioral tests for failed row actions and Lock without an approved value."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QLineEdit, QMessageBox, QPushButton

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.domain.models import FieldProposal
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import detail_panel as detail_panel_module
from infiniteprep.ui.detail_panel import (
    COLUMN_ACTIONS,
    COLUMN_APPROVED_EDIT,
    DISPLAY_FIELDS,
    DetailPanel,
)
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.qt_dialogs import MESSAGE_ICON_INDEX, MESSAGE_TEXT_INDEX
from tests.fixtures.waveform_stub import keep_waveform_pool_idle

ARTIST_ROW: int = DISPLAY_FIELDS.index(FieldName.ARTIST)
TITLE_ROW: int = DISPLAY_FIELDS.index(FieldName.TITLE)
TYPED_ARTIST: str = "Typed artist"
TYPED_TITLE: str = "Typed title"
ACCEPTED_ARTIST: str = "Accepted Artist"


class SaveFailedError(RuntimeError):
    """A storage failure raised by the review service during a save."""


@pytest.fixture
def panel_and_file(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[DetailPanel, str]:
    """Import one WAV and show its rows in a detail panel."""
    # These tests cover row actions, so no waveform analysis runs.
    keep_waveform_pool_idle(monkeypatch)
    repository = Repository(DatabaseManager(":memory:"))
    track = create_wav_fixture(tmp_path / "crate" / "a.wav")
    report = ImportService(repository, MetadataTagAdapter()).import_paths([track])
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)
    file_id = report.imported_files[0].id
    panel.load_file_asset(file_id)
    return panel, file_id


def artist_edit(panel: DetailPanel) -> QLineEdit:
    """Return the edit box of the artist row."""
    widget = panel.fields_table.cellWidget(ARTIST_ROW, COLUMN_APPROVED_EDIT)
    assert isinstance(widget, QLineEdit)
    return widget


def artist_button(panel: DetailPanel, text: str) -> QPushButton:
    """Return the artist row button with the given text."""
    actions = panel.fields_table.cellWidget(ARTIST_ROW, COLUMN_ACTIONS)
    return next(button for button in actions.findChildren(QPushButton) if button.text() == text)


def test_failed_save_keeps_typed_text(
    panel_and_file: tuple[DetailPanel, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A save that raises leaves no marker behind. The next refresh keeps the typed text."""
    panel, file_id = panel_and_file
    artist_edit(panel).setText(TYPED_ARTIST)

    def fail_save(*_args: object, **_kwargs: object) -> None:
        raise SaveFailedError("disk full")

    monkeypatch.setattr(panel.review_service, "set_manual_field_value", fail_save)
    with pytest.raises(SaveFailedError):
        panel._on_save_manual(file_id, FieldName.ARTIST.value, TYPED_ARTIST)
    panel.load_file_asset(file_id)

    assert artist_edit(panel).text() == TYPED_ARTIST


def test_refused_accept_keeps_typed_text(
    panel_and_file: tuple[DetailPanel, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accept that the review service refuses leaves the row's typed text in place."""
    panel, _file_id = panel_and_file
    artist_edit(panel).setText(TYPED_ARTIST)
    monkeypatch.setattr(panel.review_service, "accept_proposal", lambda _proposal_id: False)

    accepted = panel._accept_row_proposal(FieldName.ARTIST.value, "refused-proposal")

    assert accepted is False
    assert artist_edit(panel).text() == TYPED_ARTIST


def test_successful_accept_replaces_text(
    panel_and_file: tuple[DetailPanel, str],
) -> None:
    """An accepted proposal replaces the row's typed text. Other rows keep theirs."""
    panel, file_id = panel_and_file
    proposal = FieldProposal(
        id="accepted-artist-proposal",
        file_id=file_id,
        field_name=FieldName.ARTIST.value,
        proposed_value=ACCEPTED_ARTIST,
        provider="test-catalog",
        provider_entity_id="artist-1",
    )
    panel.repo.upsert_field_proposal(proposal)
    panel.load_file_asset(file_id)
    artist_edit(panel).setText(TYPED_ARTIST)
    title_edit = panel.fields_table.cellWidget(TITLE_ROW, COLUMN_APPROVED_EDIT)
    title_edit.setText(TYPED_TITLE)

    accepted = panel._accept_row_proposal(FieldName.ARTIST.value, proposal.id)

    assert accepted is True
    assert artist_edit(panel).text() == ACCEPTED_ARTIST
    refreshed_title = panel.fields_table.cellWidget(TITLE_ROW, COLUMN_APPROVED_EDIT)
    assert refreshed_title.text() == TYPED_TITLE


def test_lock_without_value_explains_why(
    panel_and_file: tuple[DetailPanel, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lock on a field with no approved value says what to do and changes nothing."""
    panel, _file_id = panel_and_file
    shown = CallRecorder(MESSAGE_ICON_INDEX, MESSAGE_TEXT_INDEX)
    monkeypatch.setattr(detail_panel_module, "show_plain_message", shown)

    artist_button(panel, "Lock").click()

    assert shown == [
        (
            QMessageBox.Icon.Information,
            detail_panel_module.LOCK_NEEDS_VALUE_TEXT.format(field="artist"),
        )
    ]
    assert artist_button(panel, "Lock").text() == "Lock"
