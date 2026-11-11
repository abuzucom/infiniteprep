"""Behavioral tests for the batch queue in the main window: order, close, locks, results."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from infiniteprep.adapters.analyzers.mock_analyzer import MockAudioAnalyzerAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import TaskStatus
from infiniteprep.services.import_service import ImportBatchReport, ImportService
from infiniteprep.services.job_orchestrator import BatchKind, BatchState, JobOrchestrator
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.folder_import import ImportReportDialog
from infiniteprep.ui.main_window import CLOSE_CANCEL, CLOSE_STAY, MainWindow
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.batch_jobs import GatedJobs
from tests.fixtures.memory_settings import MemorySettingsManager

TRACK_COUNT = 2
SIGNAL_TIMEOUT_MS = 10_000

WindowParts = tuple[MainWindow, Repository, GatedJobs, Path]


@pytest.fixture
def window_parts(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[WindowParts]:
    """Build a main window with a real Analyze runner, gated jobs, and a folder of tracks.

    Teardown opens the gate and waits for the queue, so no worker thread outlives its
    test and the window closes without a prompt.
    """
    monkeypatch.setattr(main_window_module, "SettingsManager", MemorySettingsManager)
    repository = Repository(DatabaseManager(tmp_path / "library.sqlite3"))
    orchestrator = JobOrchestrator(repository=repository, audio_analyzer=MockAudioAnalyzerAdapter())

    def run_analyze(file_ids: list[str], should_cancel: Any, progress: Any) -> Any:
        return orchestrator.process_files_continuing(
            file_ids, should_cancel=should_cancel, progress=progress
        )

    window = MainWindow(
        repository,
        ImportService(repository),
        ReviewService(repository),
        analyze_runner=run_analyze,
    )
    qtbot.addWidget(window)
    folder = tmp_path / "crate"
    for index in range(TRACK_COUNT):
        create_wav_fixture(folder / f"track{index}.wav", duration_sec=0.5 + index)
    jobs = GatedJobs()

    yield window, repository, jobs, folder
    jobs.gate.set()
    qtbot.waitUntil(lambda: not window.batch_queue.is_busy(), timeout=SIGNAL_TIMEOUT_MS)


def test_import_waits_behind_a_running_batch_and_opens_no_report(
    qtbot: Any, window_parts: WindowParts
) -> None:
    """A queued import starts after the running batch ends. No report opens by itself."""
    window, repository, jobs, folder = window_parts
    first = window.queue_batch(BatchKind.ANALYZE, "first", jobs.make("first"))
    assert window.start_folder_import(folder) is True
    last = window.queue_batch(BatchKind.WRITE, "last", jobs.make("last"))
    queued_import = window.batch_queue.queue.waiting()[0]

    qtbot.waitUntil(lambda: jobs.log == ["first"], timeout=SIGNAL_TIMEOUT_MS)
    assert not window.import_controller.is_running()
    assert repository.list_files() == []

    with qtbot.waitSignal(window.batch_queue.idle, timeout=SIGNAL_TIMEOUT_MS):
        jobs.gate.set()

    assert window.batch_queue.queue.finished() == [first, queued_import, last]
    assert queued_import.kind is BatchKind.IMPORT
    assert queued_import.state is BatchState.COMPLETED
    assert isinstance(queued_import.result, ImportBatchReport)
    assert window.file_table.rowCount() == TRACK_COUNT
    assert window.findChildren(ImportReportDialog) == []
    assert window.import_failure_box is None
    assert window.batch_result_box is None


def test_identify_and_analyze_runs_as_a_queued_batch(qtbot: Any, window_parts: WindowParts) -> None:
    """The toolbar action queues the visible files, and the worker analyzes each one."""
    window, repository, _jobs, folder = window_parts
    with qtbot.waitSignal(window.batch_queue.idle, timeout=SIGNAL_TIMEOUT_MS):
        window.start_folder_import(folder)

    with qtbot.waitSignal(window.batch_queue.idle, timeout=SIGNAL_TIMEOUT_MS):
        window._trigger_analyze_all()
        analyze_batch = window.batch_queue.queue.running
        assert analyze_batch is not None
        assert analyze_batch.kind is BatchKind.ANALYZE

    assert analyze_batch.state is BatchState.COMPLETED
    assert analyze_batch.result.completed_files == TRACK_COUNT
    statuses = {asset.task_status for asset in repository.list_files()}
    assert statuses == {TaskStatus.COMPLETED}


def test_library_changing_actions_lock_while_a_batch_is_active(
    qtbot: Any, window_parts: WindowParts
) -> None:
    """Transcode, journal, Bulk Approve, and field edits stay off until the queue is idle."""
    window, _repository, jobs, _folder = window_parts
    locked = (
        window.bulk_btn,
        window.transcode_btn,
        window.journal_btn,
        window.detail_panel.fields_table,
    )
    assert all(widget.isEnabled() for widget in locked)

    window.queue_batch(BatchKind.ANALYZE, "first", jobs.make("first"))

    assert not any(widget.isEnabled() for widget in locked)
    assert window.export_playlist_btn.isEnabled()
    assert window.search_input.isEnabled()

    with qtbot.waitSignal(window.batch_queue.idle, timeout=SIGNAL_TIMEOUT_MS):
        jobs.gate.set()

    assert all(widget.isEnabled() for widget in locked)


def test_keep_working_leaves_the_window_open_and_the_queue_running(
    qtbot: Any, window_parts: WindowParts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep Working ignores the close. Every batch still runs to its end."""
    window, _repository, jobs, _folder = window_parts
    monkeypatch.setattr(window, "ask_close_during_import", lambda: CLOSE_STAY)
    window.show()
    first = window.queue_batch(BatchKind.ANALYZE, "first", jobs.make("first"))
    second = window.queue_batch(BatchKind.ANALYZE, "second", jobs.make("second"))

    window.close()
    assert window.isVisible()

    with qtbot.waitSignal(window.batch_queue.idle, timeout=SIGNAL_TIMEOUT_MS):
        jobs.gate.set()

    assert window.isVisible()
    assert [first.state, second.state] == [BatchState.COMPLETED, BatchState.COMPLETED]
    assert jobs.log == ["first", "second"]


def test_cancel_and_exit_cancels_the_queue_then_closes(
    qtbot: Any, window_parts: WindowParts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel and Exit stops the running batch, drops waiting ones, and closes the window."""
    window, _repository, jobs, _folder = window_parts
    prompts: list[int] = []

    def answer_cancel() -> str:
        prompts.append(1)
        return CLOSE_CANCEL

    monkeypatch.setattr(window, "ask_close_during_import", answer_cancel)
    window.show()
    first = window.queue_batch(BatchKind.ANALYZE, "first", jobs.make("first"))
    second = window.queue_batch(BatchKind.WRITE, "second", jobs.make("second"))
    qtbot.waitUntil(lambda: jobs.log == ["first"], timeout=SIGNAL_TIMEOUT_MS)

    window.close()
    window.close()
    assert window.isVisible()
    assert second.state is BatchState.CANCELLED
    assert first.cancel_requested

    jobs.gate.set()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=SIGNAL_TIMEOUT_MS)

    assert first.state is BatchState.CANCELLED
    assert jobs.log == ["first"]
    assert prompts == [1]


def test_an_idle_window_closes_without_a_prompt(
    qtbot: Any, window_parts: WindowParts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no batch running or waiting, close asks nothing."""
    window, _repository, _jobs, _folder = window_parts
    prompts: list[int] = []
    monkeypatch.setattr(window, "ask_close_during_import", lambda: prompts.append(1))
    window.show()

    window.close()

    assert not window.isVisible()
    assert prompts == []


@pytest.mark.parametrize(
    "button_case", [("Keep Working", CLOSE_STAY), ("Cancel and Exit", CLOSE_CANCEL)]
)
def test_close_prompt_offers_exactly_the_two_choices(
    qtbot: Any, window_parts: WindowParts, button_case: tuple[str, str]
) -> None:
    """The prompt holds Keep Working and Cancel and Exit, and returns the clicked choice."""
    button_text, expected_choice = button_case
    window, _repository, _jobs, _folder = window_parts
    seen_buttons: list[str] = []

    def click_choice() -> None:
        box = window.findChildren(QMessageBox)[0]
        by_text = {button.text().replace("&", ""): button for button in box.buttons()}
        seen_buttons.extend(sorted(by_text))
        button = by_text.get(button_text)
        # A missing button still ends the prompt, so the assertions below report it.
        if button is None:
            box.reject()
            return
        button.click()

    QTimer.singleShot(0, click_choice)

    assert window.ask_close_during_import() == expected_choice
    assert seen_buttons == ["Cancel and Exit", "Keep Working"]


def test_view_result_shows_a_job_result_in_plain_text(
    qtbot: Any, window_parts: WindowParts
) -> None:
    """A finished batch opens its counts only when asked."""
    window, _repository, jobs, _folder = window_parts
    jobs.gate.set()
    with qtbot.waitSignal(window.batch_queue.idle, timeout=SIGNAL_TIMEOUT_MS):
        first = window.queue_batch(BatchKind.ANALYZE, "first", jobs.make("first"))
    assert window.batch_result_box is None

    assert window.batch_dock.select_batch(first.id)
    window.batch_dock.view_result_btn.click()

    box = window.batch_result_box
    assert box is not None
    assert box.text() == "first: Completed\nProcessed: 1. Failed: 0."
    box.close()


def test_view_result_opens_the_import_report(qtbot: Any, window_parts: WindowParts) -> None:
    """A finished import opens the import report dialog from View Result."""
    window, _repository, _jobs, folder = window_parts
    with qtbot.waitSignal(window.batch_queue.idle, timeout=SIGNAL_TIMEOUT_MS):
        window.start_folder_import(folder)
    queued_import = window.batch_queue.queue.finished()[0]

    window.show_batch_result(queued_import)

    dialogs = window.findChildren(ImportReportDialog)
    assert len(dialogs) == 1
    assert f"Imported: {TRACK_COUNT}" in dialogs[0].report_text
    dialogs[0].close()


def test_a_failed_import_shows_fixed_text_only_on_request(
    qtbot: Any, window_parts: WindowParts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed import opens nothing by itself. View Result shows text with no error detail."""
    window, repository, _jobs, folder = window_parts

    def refuse_batch() -> None:
        raise OSError("disk I/O error")

    monkeypatch.setattr(repository, "batch", refuse_batch)
    with qtbot.waitSignal(window.batch_queue.idle, timeout=SIGNAL_TIMEOUT_MS):
        window.start_folder_import(folder)
    failed_import = window.batch_queue.queue.finished()[0]
    assert failed_import.state is BatchState.FAILED
    assert window.import_failure_box is None

    window.show_batch_result(failed_import)

    box = window.import_failure_box
    assert box is not None
    assert box.text() == main_window_module.IMPORT_FAILURE_TEXT
    assert "disk I/O" not in box.text()
    box.close()
