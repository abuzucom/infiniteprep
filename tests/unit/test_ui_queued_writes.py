"""Behavioral tests for Apply Safe Writes as a queued batch."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from infiniteprep import main as main_module
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.hashing import compute_sha256
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import BatchKind, BatchState
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteBatchResult, WriteService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui import write_plan_dialog as write_plan_dialog_module
from infiniteprep.ui.main_window import MainWindow
from infiniteprep.ui.write_plan_dialog import WritePlanDialog
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.memory_settings import MemorySettingsManager

TRACK_COUNT = 2
SIGNAL_TIMEOUT_MS = 10_000

WriteParts = tuple[MainWindow, Repository, WriteService, list[str]]


@pytest.fixture
def write_parts(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[WriteParts]:
    """Build a main window over a library of two tracks that each hold an approved edit."""
    monkeypatch.setattr(main_window_module, "SettingsManager", MemorySettingsManager)
    monkeypatch.setattr(main_module, "SettingsManager", MemorySettingsManager)
    repository = Repository(DatabaseManager(tmp_path / "library.sqlite3"))
    tag_adapter = MetadataTagAdapter()
    for index in range(TRACK_COUNT):
        create_wav_fixture(
            tmp_path / "crate" / f"track{index}.wav",
            duration_sec=0.5,
            tags={"artist": "Old", "title": f"T{index}"},
        )
    report = ImportService(repository, tag_adapter).import_paths([tmp_path / "crate"])
    file_ids = [asset.id for asset in report.imported_files]
    review = ReviewService(repository)
    for file_id in file_ids:
        review.set_manual_field_value(file_id, FieldName.ARTIST.value, "Sasha")
    write_svc = WriteService(repository, tag_adapter, backup_dir=tmp_path / "backups")
    window = MainWindow(repository, ImportService(repository, tag_adapter), review)
    qtbot.addWidget(window)

    yield window, repository, write_svc, file_ids
    qtbot.waitUntil(lambda: not window.batch_queue.is_busy(), timeout=SIGNAL_TIMEOUT_MS)


def test_dialog_submits_the_safe_plans_and_writes_nothing_itself(
    qtbot: Any, write_parts: WriteParts
) -> None:
    """With submit_plans, Apply hands over the previewed safe plans and closes."""
    _window, repository, write_svc, file_ids = write_parts
    submitted: list[list[FileWritePlan]] = []
    dialog = WritePlanDialog(write_svc, file_ids, submit_plans=submitted.append)
    qtbot.addWidget(dialog)

    dialog.execute_safe_writes()

    assert len(submitted) == 1
    assert [plan.file_id for plan in submitted[0]] == file_ids
    assert all(plan.is_safe_to_apply for plan in submitted[0])
    assert repository.list_change_records() == []
    assert dialog.result() == WritePlanDialog.DialogCode.Accepted


def test_dialog_keeps_plans_back_while_a_recovery_is_pending(
    qtbot: Any, write_parts: WriteParts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending recovery shows the warning, submits nothing, and leaves the dialog open."""
    _window, _repository, write_svc, file_ids = write_parts
    submitted: list[list[FileWritePlan]] = []
    warnings: list[str] = []
    # The real QMessageBox.warning blocks on a modal box, so a recorder replaces it.
    recorder = SimpleNamespace(warning=lambda _parent, _title, text: warnings.append(text))
    monkeypatch.setattr(write_plan_dialog_module, "QMessageBox", recorder)
    dialog = WritePlanDialog(write_svc, file_ids, submit_plans=submitted.append)
    qtbot.addWidget(dialog)
    write_svc._journal_in_progress(dialog.current_plans[0], None)

    dialog.execute_safe_writes()

    assert submitted == []
    assert len(warnings) == 1
    assert "interrupted write" in warnings[0]
    assert dialog.result() != WritePlanDialog.DialogCode.Accepted


def test_queued_writes_run_on_the_queue_and_change_the_files(
    qtbot: Any, write_parts: WriteParts
) -> None:
    """queue_safe_writes adds one write batch. The worker applies each previewed plan."""
    window, repository, write_svc, file_ids = write_parts
    plans = write_svc.build_batch_preview(file_ids)
    hashes_before = [compute_sha256(plan.source_path) for plan in plans]

    with qtbot.waitSignal(window.batch_queue.idle, timeout=SIGNAL_TIMEOUT_MS):
        main_module.queue_safe_writes(window, write_svc, plans)
        batch = window.batch_queue.queue.running
        assert batch is not None
        assert batch.kind is BatchKind.WRITE
        assert batch.label == f"Apply writes to {TRACK_COUNT} file(s)"

    assert batch.state is BatchState.COMPLETED
    assert isinstance(batch.result, WriteBatchResult)
    assert len(batch.result.records) == TRACK_COUNT
    assert batch.result.failures == []
    assert [compute_sha256(plan.source_path) for plan in plans] != hashes_before
    statuses = {record.operation_status for record in repository.list_change_records()}
    assert statuses == {OperationStatus.COMPLETED}


def test_stop_setting_reaches_a_queued_write_batch(
    qtbot: Any, write_parts: WriteParts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the stop setting saved, a queued batch ends at its first failed write."""
    window, repository, write_svc, file_ids = write_parts
    plans = write_svc.build_batch_preview(file_ids)

    def refuse_backup(_source: Path, _backup: Path, _expected_hash: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(write_svc.tag_adapter, "_create_verified_backup", refuse_backup)
    monkeypatch.setattr(main_module, "stop_on_first_failure", lambda: True)

    with qtbot.waitSignal(window.batch_queue.idle, timeout=SIGNAL_TIMEOUT_MS):
        main_module.queue_safe_writes(window, write_svc, plans)
    batch = window.batch_queue.queue.finished()[0]

    assert isinstance(batch.result, WriteBatchResult)
    assert len(batch.result.failures) == 1
    assert batch.result.stopped_early is True
    assert batch.state is BatchState.COMPLETED
    assert repository.list_change_records(plans[1].file_id) == []
