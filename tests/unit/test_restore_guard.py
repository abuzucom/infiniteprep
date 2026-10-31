"""Tests that restore confirms its target and never silently overwrites later edits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter, compute_sha256
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import RestoreConflictError, WriteService
from infiniteprep.ui import journal_dialog as journal_module
from infiniteprep.ui.journal_dialog import COL_RESTORE_ACTION, JournalDialog
from tests.fixtures.audio_factory import create_wav_fixture

LATER_EDIT = b"edited in another app after the write"


@pytest.fixture
def written(tmp_path: Path) -> tuple[Repository, WriteService, str, Path, str, Path]:
    """Import one tagged WAV, write a new artist, and return the completed operation id."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MutagenAdapter()
    source = create_wav_fixture(
        tmp_path / "crate" / "track.wav", duration_sec=1.0, tags={"artist": "Old", "title": "T"}
    )
    original_hash = compute_sha256(source)
    asset = ImportService(repo, tag_adapter).import_paths([source]).imported_files[0]
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    backup_dir = tmp_path / "backups"
    write_svc = WriteService(repo, tag_adapter, backup_dir=backup_dir)
    record = write_svc.apply_single_plan(write_svc.build_batch_preview([asset.id])[0])
    return repo, write_svc, record.operation_id, source, original_hash, backup_dir


def pre_restore_copies(backup_dir: Path) -> list[bytes]:
    """Return the contents of every file saved before a restore."""
    return [path.read_bytes() for path in sorted(backup_dir.glob("*_pre_restore_*"))]


def test_restore_refuses_a_file_edited_after_the_write(written: Any) -> None:
    """A later edit raises RestoreConflictError, and the edited file stays as it is."""
    repo, write_svc, operation_id, source, _, _ = written
    source.write_bytes(LATER_EDIT)

    with pytest.raises(RestoreConflictError, match="changed after this write"):
        write_svc.restore_operation(operation_id)

    assert source.read_bytes() == LATER_EDIT
    stored = repo.get_change_record(operation_id)
    assert stored is not None
    assert stored.operation_status == OperationStatus.COMPLETED


def test_a_confirmed_restore_saves_the_later_edit_first(written: Any) -> None:
    """allow_modified restores the original after copying the edited file to the backups."""
    _, write_svc, operation_id, source, original_hash, backup_dir = written
    source.write_bytes(LATER_EDIT)

    write_svc.restore_operation(operation_id, allow_modified=True)

    assert compute_sha256(source) == original_hash
    assert pre_restore_copies(backup_dir) == [LATER_EDIT]


def test_an_unchanged_restore_also_keeps_the_written_file(written: Any) -> None:
    """A restore with no later edit still saves the written file before replacing it."""
    _, write_svc, operation_id, source, original_hash, backup_dir = written
    written_bytes = source.read_bytes()

    write_svc.restore_operation(operation_id)

    assert compute_sha256(source) == original_hash
    assert pre_restore_copies(backup_dir) == [written_bytes]


def answer_questions(monkeypatch: pytest.MonkeyPatch, answers: list[bool]) -> list[str]:
    """Answer each journal confirmation in order, and record the question texts."""
    asked: list[str] = []

    def fake_question(_parent: Any, _title: str, text: str) -> bool:
        asked.append(text)
        return answers[len(asked) - 1]

    monkeypatch.setattr(journal_module, "ask_plain_question", fake_question)
    return asked


def click_restore(qtbot: Any, repo: Repository, write_svc: WriteService) -> None:
    """Open the journal and click the Restore Backup button of its only row."""
    dialog = JournalDialog(repo, write_svc)
    qtbot.addWidget(dialog)
    dialog.table.cellWidget(0, COL_RESTORE_ACTION).click()


def test_declining_the_journal_confirmation_changes_nothing(
    qtbot, written: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirmation names the target file, and No leaves the written file in place."""
    repo, write_svc, operation_id, source, _, _ = written
    written_hash = compute_sha256(source)
    asked = answer_questions(monkeypatch, [False])

    click_restore(qtbot, repo, write_svc)

    assert len(asked) == 1
    assert source.name in asked[0]
    assert compute_sha256(source) == written_hash
    stored = repo.get_change_record(operation_id)
    assert stored is not None
    assert stored.operation_status == OperationStatus.COMPLETED


def test_the_journal_asks_again_before_overwriting_a_later_edit(
    qtbot, written: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later edit triggers a second question, and Yes restores the original."""
    repo, write_svc, _, source, original_hash, _ = written
    source.write_bytes(LATER_EDIT)
    asked = answer_questions(monkeypatch, [True, True])

    click_restore(qtbot, repo, write_svc)

    assert len(asked) == 2
    assert "changed after this write" in asked[1]
    assert compute_sha256(source) == original_hash
