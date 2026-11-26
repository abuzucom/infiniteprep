"""Tests for write preview files that have nothing to change."""

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
from infiniteprep.ui.write_plan_dialog import COLUMN_PLAN_STATUS, WritePlanDialog
from infiniteprep.ui.write_preview_model import (
    NOTHING_TO_CHANGE_REASON,
    READY_LABEL,
    SKIPPED_LABEL,
    PreviewCounts,
    count_plans,
    plan_status_label,
    plan_status_reasons,
    plan_will_write,
)
from tests.fixtures.audio_factory import create_wav_fixture

OLD_ARTIST = "Old"
NEW_ARTIST = "Sasha"


def make_plan(
    *,
    existing: dict[str, str] | None = None,
    proposed: dict[str, str] | None = None,
    destination: str = "/music/a.mp3",
    rename: bool = False,
) -> FileWritePlan:
    """Return a safe write plan with the given tag values and destination."""
    return FileWritePlan(
        file_id="file-1",
        source_path=Path("/music/a.mp3"),
        destination_path=Path(destination),
        rename_enabled=rename,
        expected_content_hash="hash",
        existing_tags=existing or {},
        proposed_tags_to_write=proposed or {},
        preserved_custom_tags={},
        backup_destination_path=Path("/backups/a.mp3"),
        validation_errors=[],
        tag_data_warnings=[],
    )


def test_same_values_skip_with_reason() -> None:
    """A safe file whose new values match its tags reads Skipped and says why."""
    plan = make_plan(existing={"artist": OLD_ARTIST}, proposed={"artist": OLD_ARTIST})

    assert not plan_will_write(plan)
    assert plan_status_label(plan) == SKIPPED_LABEL
    assert plan_status_reasons(plan) == NOTHING_TO_CHANGE_REASON


def test_field_change_or_rename_writes() -> None:
    """A changed field or a new file name each count as a change."""
    changed = make_plan(existing={"artist": OLD_ARTIST}, proposed={"artist": NEW_ARTIST})
    renamed = make_plan(destination="/music/b.mp3", rename=True)
    same_name = make_plan(destination="/music/a.mp3", rename=True)

    assert plan_will_write(changed)
    assert plan_status_label(changed) == READY_LABEL
    assert plan_will_write(renamed)
    assert not plan_will_write(same_name)


def test_counts_leave_out_unchanged_files() -> None:
    """An unchanged file counts as skipped, not as a file that will change."""
    plans = [
        make_plan(existing={"artist": OLD_ARTIST}, proposed={"artist": NEW_ARTIST}),
        make_plan(existing={"artist": OLD_ARTIST}, proposed={"artist": OLD_ARTIST}),
    ]

    assert count_plans(plans) == PreviewCounts(will_change=1, removes_tag_data=0, skipped=1)


def test_save_leaves_out_unchanged_files(qtbot: Any, tmp_path: Path) -> None:
    """Save hands over only the file with a change. The unchanged one shows Skipped."""
    repository = Repository(DatabaseManager(":memory:"))
    tag_adapter = MetadataTagAdapter()
    tracks = [
        create_wav_fixture(tmp_path / "crate" / f"t{index}.wav", tags={"artist": OLD_ARTIST})
        for index in range(2)
    ]
    report = ImportService(repository, tag_adapter).import_paths(tracks)
    changed_id, unchanged_id = (asset.id for asset in report.imported_files)
    review = ReviewService(repository)
    review.set_manual_field_value(changed_id, FieldName.ARTIST.value, NEW_ARTIST)
    review.set_manual_field_value(unchanged_id, FieldName.ARTIST.value, OLD_ARTIST)
    write_service = WriteService(repository, tag_adapter, backup_dir=tmp_path / "backups")
    submitted: list[list[FileWritePlan]] = []
    dialog = WritePlanDialog(
        write_service, [changed_id, unchanged_id], submit_plans=submitted.append
    )
    qtbot.addWidget(dialog)
    statuses = {
        dialog.current_plans[index].file_id: dialog.plan_tree.topLevelItem(index).text(
            COLUMN_PLAN_STATUS
        )
        for index in range(dialog.plan_tree.topLevelItemCount())
    }

    dialog.execute_safe_writes()

    assert statuses == {changed_id: READY_LABEL, unchanged_id: SKIPPED_LABEL}
    assert dialog.apply_btn.text() == "Save changes to 1 file"
    assert [[plan.file_id for plan in plans] for plans in submitted] == [[changed_id]]
