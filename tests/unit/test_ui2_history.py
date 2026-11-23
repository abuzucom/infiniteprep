"""Tests for History: the write list, the chosen write's diff, the backup state, and Restore."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.domain.models import ChangeRecord
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.history_model import (
    BACKUP_FAILED_TEXT,
    BACKUP_PENDING_TEXT,
    BACKUP_READY_TEXT,
    BACKUP_USED_TEXT,
    NO_SELECTION_TEXT,
    backup_state_text,
    can_restore,
    history_status_label,
    record_field_diff,
)
from infiniteprep.ui.journal_dialog import COLUMN_FILE, COLUMN_STATUS, JournalDialog
from tests.fixtures.audio_factory import create_wav_fixture

OLD_ARTIST = "Old Artist"
NEW_ARTIST = "Sasha"


def make_record(status: OperationStatus) -> ChangeRecord:
    """Return a journal record in the given state that wrote one artist."""
    return ChangeRecord(
        operation_id="op-1",
        file_id="file-1",
        previous_path="/music/a.wav",
        new_path="/music/a.wav",
        previous_tags={"artist": OLD_ARTIST},
        written_tags={"artist": NEW_ARTIST},
        backup_reference="/backups/a.wav",
        operation_status=status,
        timestamp="2026-11-22T12:00:00+00:00",
    )


def test_status_words_are_plain() -> None:
    """Each stored state reads as a plain word, and an unknown value shows as stored."""
    assert history_status_label("completed") == "Saved"
    assert history_status_label("rolled_back") == "Undone"
    assert history_status_label("in_progress") == "Stopped partway"
    assert history_status_label("not_a_state") == "not_a_state"


def test_backup_state_follows_status() -> None:
    """The backup sentence and Restore follow the state of the change."""
    assert backup_state_text(make_record(OperationStatus.COMPLETED)) == BACKUP_READY_TEXT
    assert backup_state_text(make_record(OperationStatus.RESTORED)) == BACKUP_USED_TEXT
    assert backup_state_text(make_record(OperationStatus.FAILED)) == BACKUP_FAILED_TEXT
    assert backup_state_text(make_record(OperationStatus.TAGS_WRITTEN)) == BACKUP_PENDING_TEXT
    assert can_restore(make_record(OperationStatus.COMPLETED))
    assert not can_restore(make_record(OperationStatus.RESTORED))


def test_diff_pairs_before_and_after() -> None:
    """The diff pairs each written field with its value before the change."""
    record = make_record(OperationStatus.COMPLETED)

    assert record_field_diff(record, str.title) == [("Artist", OLD_ARTIST, NEW_ARTIST)]


def test_empty_history_shows_no_detail(qtbot: Any) -> None:
    """With no writes, the detail side asks for a pick and Restore stays off."""
    repository = Repository(DatabaseManager(":memory:"))
    dialog = JournalDialog(repository, WriteService(repository))
    qtbot.addWidget(dialog)

    assert dialog.detail_title.text() == NO_SELECTION_TEXT
    assert not dialog.restore_btn.isEnabled()


def test_history_shows_the_chosen_write(qtbot: Any, tmp_path: Path) -> None:
    """The newest write opens chosen, with its diff, its backup state, and Restore on."""
    repository = Repository(DatabaseManager(":memory:"))
    tag_adapter = MetadataTagAdapter()
    track = create_wav_fixture(tmp_path / "crate" / "a.wav", tags={"artist": OLD_ARTIST})
    report = ImportService(repository, tag_adapter).import_paths([track])
    file_id = report.imported_files[0].id
    ReviewService(repository).set_manual_field_value(file_id, FieldName.ARTIST.value, NEW_ARTIST)
    write_service = WriteService(repository, tag_adapter, backup_dir=tmp_path / "backups")
    write_service.apply_single_plan(write_service.build_batch_preview([file_id])[0])

    dialog = JournalDialog(repository, write_service)
    qtbot.addWidget(dialog)

    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, COLUMN_FILE).text() == "a.wav"
    assert dialog.table.item(0, COLUMN_STATUS).text() == "Saved"
    assert dialog.detail_title.text() == "a.wav"
    diff = [
        tuple(dialog.diff_table.item(row, column).text() for column in range(3))
        for row in range(dialog.diff_table.rowCount())
    ]
    assert ("Artist", OLD_ARTIST, NEW_ARTIST) in diff
    assert dialog.backup_label.text() == BACKUP_READY_TEXT
    assert dialog.restore_btn.isEnabled()
