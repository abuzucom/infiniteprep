"""Tests for the write preview: summary counts, the file button, and the field diff tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.write_plan_dialog import (
    COLUMN_PLAN_FILE,
    COLUMN_PLAN_NEW,
    COLUMN_PLAN_OLD,
    COLUMN_PLAN_STATUS,
    WritePlanDialog,
)
from infiniteprep.ui.write_preview_model import (
    READY_LABEL,
    READY_WITH_LOSS_LABEL,
    SKIPPED_LABEL,
    PreviewCounts,
    count_plans,
    format_apply_label,
    format_summary_badges,
    plan_field_diff,
    plan_status_label,
)
from tests.fixtures.audio_factory import create_wav_fixture

NEW_ARTIST = "Sasha"


def make_plan(
    *,
    errors: list[str] | None = None,
    losses: list[str] | None = None,
    existing: dict[str, str] | None = None,
    proposed: dict[str, str] | None = None,
) -> FileWritePlan:
    """Return a write plan with the given errors, data losses, and tag values."""
    return FileWritePlan(
        file_id="file-1",
        source_path=Path("/music/a.mp3"),
        destination_path=Path("/music/a.mp3"),
        rename_enabled=False,
        expected_content_hash="hash",
        existing_tags=existing or {},
        proposed_tags_to_write=proposed or {},
        preserved_custom_tags={},
        backup_destination_path=Path("/backups/a.mp3"),
        validation_errors=errors or [],
        tag_data_warnings=losses or [],
    )


def test_counts_split_ready_loss_skipped() -> None:
    """Safe files count as changing, data loss counts inside them, and blocked ones skip."""
    plans = [
        make_plan(proposed={"artist": "New"}),
        make_plan(losses=["drops GEOB"], proposed={"artist": "New"}),
        make_plan(errors=["missing"]),
    ]

    assert count_plans(plans) == PreviewCounts(will_change=2, removes_tag_data=1, skipped=1)


def test_button_names_the_file_count() -> None:
    """The primary button names how many files it saves."""
    assert format_apply_label(PreviewCounts(1, 0, 0)) == "Save changes to 1 file"
    assert format_apply_label(PreviewCounts(3, 0, 2)) == "Save changes to 3 files"
    assert format_apply_label(PreviewCounts(0, 0, 2)) == "Save changes"


def test_badges_read_as_counts() -> None:
    """The summary badges read as plain counts."""
    assert format_summary_badges(PreviewCounts(2, 1, 0)) == (
        "2 files will change",
        "1 file loses some tag data",
        "0 files skipped",
    )
    assert format_summary_badges(PreviewCounts(1, 2, 3))[1] == "2 files lose some tag data"


def test_status_labels_are_plain() -> None:
    """A file reads as ready, ready with tag data loss, or skipped."""
    assert plan_status_label(make_plan(proposed={"artist": "New"})) == READY_LABEL
    assert (
        plan_status_label(make_plan(losses=["drops GEOB"], proposed={"artist": "New"}))
        == READY_WITH_LOSS_LABEL
    )
    assert plan_status_label(make_plan(errors=["missing"])) == SKIPPED_LABEL


def test_diff_lists_only_changed_fields() -> None:
    """The diff lists each field whose new value differs, by its plain name."""
    plan = make_plan(
        existing={"artist": "Old", "title": "Same"},
        proposed={"artist": "New", "title": "Same", "mix_name": "Dub"},
    )

    assert plan_field_diff(plan, str.upper) == [("ARTIST", "Old", "New"), ("MIX_NAME", "", "Dub")]


def test_dialog_rows_expand_to_the_diff(qtbot: Any, tmp_path: Path) -> None:
    """Each file row holds one child per changed field, and the button names the count."""
    repository = Repository(DatabaseManager(":memory:"))
    tag_adapter = MetadataTagAdapter()
    track = create_wav_fixture(tmp_path / "crate" / "a.wav", tags={"artist": "Old"})
    report = ImportService(repository, tag_adapter).import_paths([track])
    file_id = report.imported_files[0].id
    ReviewService(repository).set_manual_field_value(file_id, FieldName.ARTIST.value, NEW_ARTIST)
    write_service = WriteService(repository, tag_adapter, backup_dir=tmp_path / "backups")

    dialog = WritePlanDialog(write_service, [file_id])
    qtbot.addWidget(dialog)

    tree = dialog.plan_tree
    assert tree.topLevelItemCount() == 1
    file_row = tree.topLevelItem(0)
    assert file_row.text(COLUMN_PLAN_FILE) == "a.wav"
    assert file_row.text(COLUMN_PLAN_STATUS) == READY_LABEL
    assert file_row.isExpanded()
    changes = [
        (
            file_row.child(index).text(COLUMN_PLAN_FILE),
            file_row.child(index).text(COLUMN_PLAN_OLD),
            file_row.child(index).text(COLUMN_PLAN_NEW),
        )
        for index in range(file_row.childCount())
    ]
    assert ("Artist", "Old", NEW_ARTIST) in changes
    assert dialog.apply_btn.text() == "Save changes to 1 file"
    assert dialog.change_badge.text() == "1 file will change"
    assert dialog.skipped_badge.isHidden()
