"""Tests for the startup dialog that resolves interrupted writes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QMessageBox

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    MetadataTagAdapter,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.hashing import compute_sha256
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui import recovery_dialog as recovery_module
from infiniteprep.ui.recovery_dialog import COLUMN_KEEP, COLUMN_RESTORE, RecoveryDialog
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.qt_dialogs import MESSAGE_ICON_INDEX, MESSAGE_TEXT_INDEX, QUESTION_TEXT_INDEX

CORRUPT_BYTES = b"half-written"
CORRUPT_HASH = hashlib.sha256(CORRUPT_BYTES).hexdigest()


@pytest.fixture
def interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Repository, WriteService, Path, str]:
    """Import one tagged WAV and stop a real write after it corrupts the file."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MetadataTagAdapter()
    source = create_wav_fixture(
        tmp_path / "crate" / "track.wav", duration_sec=1.0, tags={"artist": "Old", "title": "T"}
    )
    original_hash = compute_sha256(source)
    asset = ImportService(repo, tag_adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")

    # The tag writer only sees the staged copy, which the adapter discards. The fake
    # corrupts the source itself, as a crash after the staged copy replaced it would.
    def corrupt_then_die(_staged: Path, _tags: dict[str, str]) -> None:
        source.write_bytes(CORRUPT_BYTES)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(tag_adapter, "_write_tags_by_format", corrupt_then_die)
        with pytest.raises(KeyboardInterrupt):
            write_svc.apply_single_plan(write_svc.build_batch_preview([asset.id])[0])
    assert compute_sha256(source) == CORRUPT_HASH
    return repo, write_svc, source, original_hash


def test_restore_button_returns_the_original_and_closes_the_dialog(qtbot, interrupted: Any) -> None:
    """Clicking Restore from backup recovers the file, and the empty dialog accepts."""
    repo, write_svc, source, original_hash = interrupted
    dialog = RecoveryDialog(write_svc)
    qtbot.addWidget(dialog)
    operation_id = dialog.records[0].operation_id

    dialog.table.cellWidget(0, COLUMN_RESTORE).click()

    assert compute_sha256(source) == original_hash
    stored = repo.get_change_record(operation_id)
    assert stored is not None
    assert stored.operation_status == OperationStatus.ROLLED_BACK
    assert dialog.records == []
    assert dialog.table.rowCount() == 0


def test_keep_button_marks_the_write_finished(
    qtbot, interrupted: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clicking Mark as finished and answering Yes keeps the file and completes the record."""
    repo, write_svc, source, _ = interrupted
    # The real Yes/No box blocks on exec, so a recorder answers Yes.
    monkeypatch.setattr(recovery_module, "ask_plain_question", CallRecorder(reply=True))
    dialog = RecoveryDialog(write_svc)
    qtbot.addWidget(dialog)
    operation_id = dialog.records[0].operation_id

    dialog.table.cellWidget(0, COLUMN_KEEP).click()

    assert compute_sha256(source) == CORRUPT_HASH
    stored = repo.get_change_record(operation_id)
    assert stored is not None
    assert stored.operation_status == OperationStatus.COMPLETED
    assert write_svc.list_interrupted_operations() == []


def test_declining_mark_as_finished_keeps_the_write_pending(
    qtbot, interrupted: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No to the keep prompt leaves the interrupted write listed and unresolved."""
    repo, write_svc, _source, _hash = interrupted
    # The question recorder keeps each question text and answers No.
    questions = CallRecorder(QUESTION_TEXT_INDEX, reply=False)
    monkeypatch.setattr(recovery_module, "ask_plain_question", questions)
    dialog = RecoveryDialog(write_svc)
    qtbot.addWidget(dialog)
    operation_id = dialog.records[0].operation_id

    dialog.table.cellWidget(0, COLUMN_KEEP).click()

    assert len(questions) == 1
    assert "track.wav" in questions[0]
    stored = repo.get_change_record(operation_id)
    assert stored is not None
    assert stored.operation_status == OperationStatus.IN_PROGRESS
    assert dialog.table.rowCount() == 1


def test_a_failed_restore_warns_and_keeps_the_row(
    qtbot, interrupted: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tampered backup shows a plain-text warning, and the write stays listed."""
    repo, write_svc, _, _ = interrupted
    messages = CallRecorder(MESSAGE_ICON_INDEX, MESSAGE_TEXT_INDEX)
    monkeypatch.setattr(recovery_module, "show_plain_message", messages)
    dialog = RecoveryDialog(write_svc)
    qtbot.addWidget(dialog)
    record = repo.get_change_record(dialog.records[0].operation_id)
    assert record is not None
    Path(record.backup_reference).write_bytes(b"tampered")

    dialog.table.cellWidget(0, COLUMN_RESTORE).click()

    warnings = [text for icon, text in messages if icon == QMessageBox.Icon.Warning]
    assert len(warnings) == 1
    assert warnings[0].startswith("Restore failed")
    assert dialog.table.rowCount() == 1
