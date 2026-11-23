"""Tests for the write preview rows that start open in a short preview."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.write_plan_dialog import WritePlanDialog
from infiniteprep.ui.write_preview_model import EXPAND_ROWS_MAX_FILES, should_expand_rows
from tests.fixtures.audio_factory import create_wav_fixture

NEW_ARTIST = "Sasha"


def open_preview(qtbot: Any, tmp_path: Path, file_count: int) -> WritePlanDialog:
    """Import file_count tracks, approve a new artist on each, and open the preview."""
    repository = Repository(DatabaseManager(":memory:"))
    tag_adapter = MetadataTagAdapter()
    tracks = [
        create_wav_fixture(tmp_path / "crate" / f"t{index}.wav", tags={"artist": "Old"})
        for index in range(file_count)
    ]
    report = ImportService(repository, tag_adapter).import_paths(tracks)
    file_ids = [asset.id for asset in report.imported_files]
    review = ReviewService(repository)
    for file_id in file_ids:
        review.set_manual_field_value(file_id, FieldName.ARTIST.value, NEW_ARTIST)
    write_service = WriteService(repository, tag_adapter, backup_dir=tmp_path / "backups")
    dialog = WritePlanDialog(write_service, file_ids)
    qtbot.addWidget(dialog)
    return dialog


def expanded_rows(dialog: WritePlanDialog) -> list[bool]:
    """Return whether each file row of the preview is open."""
    tree = dialog.plan_tree
    return [tree.topLevelItem(index).isExpanded() for index in range(tree.topLevelItemCount())]


def test_rows_open_up_to_the_limit() -> None:
    """A preview of up to ten files opens its rows. A longer one keeps them closed."""
    assert EXPAND_ROWS_MAX_FILES == 10
    assert should_expand_rows(1)
    assert should_expand_rows(EXPAND_ROWS_MAX_FILES)
    assert not should_expand_rows(EXPAND_ROWS_MAX_FILES + 1)
    assert not should_expand_rows(0)


def test_short_preview_opens_every_row(qtbot: Any, tmp_path: Path) -> None:
    """Each row of a two-file preview starts open."""
    dialog = open_preview(qtbot, tmp_path, 2)

    assert expanded_rows(dialog) == [True, True]


def test_long_preview_keeps_rows_closed(qtbot: Any, tmp_path: Path) -> None:
    """Each row of an eleven-file preview starts closed."""
    dialog = open_preview(qtbot, tmp_path, EXPAND_ROWS_MAX_FILES + 1)

    assert expanded_rows(dialog) == [False] * (EXPAND_ROWS_MAX_FILES + 1)
