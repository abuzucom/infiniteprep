"""Tests for the detail panel sections, header badges, changed rows, and Technical details."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, KeyDisplayNotation
from infiniteprep.domain.models import FieldProposal
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.detail_panel import (
    CANDIDATES_SECTION_TITLE,
    CHANGED_ROW_ROLE,
    CHANGES_SECTION_TITLE,
    COLUMN_APPROVED_EDIT,
    COLUMN_FIELD_NAME,
    OTHER_TAGS_SECTION_TITLE,
    PLAYER_SECTION_TITLE,
    ROW_FIELD_PROPERTY,
    TECHNICAL_SECTION_TITLE,
    WARNINGS_SECTION_TITLE,
    DetailPanel,
)
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.waveform_stub import keep_waveform_pool_idle

SECTION_ORDER = [
    PLAYER_SECTION_TITLE,
    CHANGES_SECTION_TITLE,
    WARNINGS_SECTION_TITLE,
    OTHER_TAGS_SECTION_TITLE,
    CANDIDATES_SECTION_TITLE,
    TECHNICAL_SECTION_TITLE,
]
PROPOSED_KEY = "8A"
PROPOSED_BPM = "124"


@pytest.fixture
def panel_and_file(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[DetailPanel, str]:
    """Import one WAV and show it in a detail panel."""
    keep_waveform_pool_idle(monkeypatch)
    repository = Repository(DatabaseManager(":memory:"))
    track = create_wav_fixture(tmp_path / "crate" / "a.wav", tags={"artist": "Artist A"})
    report = ImportService(repository, MetadataTagAdapter()).import_paths([track])
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)
    file_id = report.imported_files[0].id
    panel.load_file_asset(file_id)
    return panel, file_id


def propose(panel: DetailPanel, file_id: str, field: FieldName, value: str) -> None:
    """Store one proposal for a field of the file."""
    panel.repo.upsert_field_proposal(
        FieldProposal(
            id=f"proposal-{field.value}",
            file_id=file_id,
            field_name=field.value,
            proposed_value=value,
            provider="test-catalog",
            provider_entity_id=f"entity-{field.value}",
        )
    )


def row_fields(panel: DetailPanel) -> list[str]:
    """Return the stored field key of each field row, top to bottom."""
    table = panel.fields_table
    return [
        str(table.cellWidget(row, COLUMN_APPROVED_EDIT).property(ROW_FIELD_PROPERTY))
        for row in range(table.rowCount())
    ]


def test_sections_stack_in_order(panel_and_file: tuple[DetailPanel, str]) -> None:
    """The six sections stack in design order. Only Technical details starts closed."""
    panel, _file_id = panel_and_file

    assert list(panel.sections) == SECTION_ORDER
    expanded = {title: section.is_expanded() for title, section in panel.sections.items()}
    assert expanded == {title: title != TECHNICAL_SECTION_TITLE for title in SECTION_ORDER}


def test_arrow_hides_the_section(panel_and_file: tuple[DetailPanel, str]) -> None:
    """A click on a section's arrow hides its content, and a second click shows it."""
    panel, _file_id = panel_and_file
    panel.show()
    changes = panel.sections[CHANGES_SECTION_TITLE]

    changes.toggle_button.click()
    hidden = panel.fields_table.isVisible()
    changes.toggle_button.click()

    assert hidden is False
    assert panel.fields_table.isVisible()


def test_changed_rows_come_first(panel_and_file: tuple[DetailPanel, str]) -> None:
    """Fields with a proposal move to the top in field order and show in bold."""
    panel, file_id = panel_and_file
    propose(panel, file_id, FieldName.KEY, PROPOSED_KEY)
    propose(panel, file_id, FieldName.BPM, PROPOSED_BPM)

    panel.load_file_asset(file_id)

    fields = row_fields(panel)
    assert fields[:2] == [FieldName.BPM.value, FieldName.KEY.value]
    assert sorted(fields) == sorted(field.value for field in FieldName)
    name_items = [panel.fields_table.item(row, COLUMN_FIELD_NAME) for row in range(3)]
    assert [item.data(CHANGED_ROW_ROLE) is True for item in name_items] == [True, True, False]
    assert [item.font().bold() for item in name_items] == [True, True, False]


def test_badges_status_format_bpm_key(
    panel_and_file: tuple[DetailPanel, str],
) -> None:
    """The header badges show the status, the format, the BPM, and the key."""
    panel, file_id = panel_and_file
    propose(panel, file_id, FieldName.KEY, PROPOSED_KEY)
    propose(panel, file_id, FieldName.BPM, PROPOSED_BPM)
    panel.key_notation = KeyDisplayNotation.CAMELOT

    panel.load_file_asset(file_id)

    badges = panel.badges_row
    assert badges.status_badge.text()
    assert badges.format_badge.text() == "WAV"
    assert badges.bpm_badge.text() == f"{PROPOSED_BPM} BPM"
    assert badges.key_badge.text() == PROPOSED_KEY
    assert "background" in badges.key_badge.styleSheet()


def test_clear_hides_badges(panel_and_file: tuple[DetailPanel, str]) -> None:
    """A cleared selection hides every badge and empties Technical details."""
    panel, _file_id = panel_and_file
    panel.show()

    panel.clear_selection()

    badges = panel.badges_row
    shown = [badges.status_badge, badges.format_badge, badges.bpm_badge, badges.key_badge]
    assert not any(badge.isVisible() for badge in shown)
    assert panel.technical_label.text() == ""


def test_technical_details_hold_frames_hash(
    panel_and_file: tuple[DetailPanel, str],
) -> None:
    """Technical details name each tag frame and the content hash, behind the closed toggle."""
    panel, file_id = panel_and_file
    panel.show()
    asset = panel.repo.get_file_by_id(file_id)
    assert asset is not None

    text = panel.technical_label.text()

    assert "Artist frame: TPE1" in text
    assert asset.content_hash in text
    assert "Latest backup: No backup yet" in text
    assert not panel.technical_label.isVisible()
    panel.technical_section.toggle_button.click()
    assert panel.technical_label.isVisible()


def test_warnings_sit_one_per_line(panel_and_file: tuple[DetailPanel, str]) -> None:
    """Each warning sentence sits on its own line."""
    panel, file_id = panel_and_file
    asset = panel.repo.get_file_by_id(file_id)
    assert asset is not None
    asset.original_tags.setdefault("technical", {})["warnings"] = ["First issue.", "Second."]

    panel._populate_warnings(asset)

    assert panel.warnings_label.text().splitlines() == ["First issue.", "Second."]
