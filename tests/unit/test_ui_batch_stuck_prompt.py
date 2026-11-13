"""Behavioral tests for the stuck-file prompt of queued Analyze and write batches."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QWidget

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import BatchKind, BatchState
from infiniteprep.ui.batch_queue import (
    ANALYZE_STUCK_PROMPT,
    WRITE_STUCK_PROMPT,
    BatchQueueController,
)
from infiniteprep.ui.folder_import import STUCK_EXIT_CODE, FolderImportController
from infiniteprep.ui.stuck_file_watcher import StuckPrompt
from tests.fixtures.batch_jobs import GatedJobs

SIGNAL_TIMEOUT_MS = 10_000
SHORT_TIMEOUT_MS = 100
# Long enough that no prompt appears while a test checks for its absence.
QUIET_WAIT_MS = 300
ANALYZE_TITLE = "Analysis Stuck (test)"
WRITE_TITLE = "Write Stuck (test)"

StuckParts = tuple[BatchQueueController, GatedJobs, list[int]]


@pytest.fixture
def stuck_parts(qtbot: Any, tmp_path: Path) -> Iterator[StuckParts]:
    """Build a queue with short limits whose exit call only records the code.

    Teardown opens the gate and waits for the queue, so no worker thread outlives
    its test.
    """
    parent = QWidget()
    qtbot.addWidget(parent)
    repository = Repository(DatabaseManager(tmp_path / "stuck.sqlite3"))
    import_controller = FolderImportController(
        ImportService(repository), parent, show_progress_dialog=False
    )
    exit_codes: list[int] = []
    controller = BatchQueueController(
        import_controller,
        repository.db.close,
        parent,
        prompt_parent=parent,
        stuck_prompts={
            BatchKind.ANALYZE: StuckPrompt(ANALYZE_TITLE, "analyze text", SHORT_TIMEOUT_MS),
            BatchKind.WRITE: StuckPrompt(WRITE_TITLE, "write text", SHORT_TIMEOUT_MS),
        },
        exit_process=exit_codes.append,
    )
    jobs = GatedJobs()
    yield controller, jobs, exit_codes
    jobs.gate.set()
    qtbot.waitUntil(lambda: not controller.is_busy(), timeout=SIGNAL_TIMEOUT_MS)


def wait_for_prompt(qtbot: Any, controller: BatchQueueController) -> Any:
    """Wait until the stuck prompt is open and return it."""
    watcher = controller.stuck_watcher
    qtbot.waitUntil(lambda: watcher.stuck_box is not None, timeout=SIGNAL_TIMEOUT_MS)
    return watcher.stuck_box


@pytest.mark.parametrize(
    "kind_case", [(BatchKind.ANALYZE, ANALYZE_TITLE), (BatchKind.WRITE, WRITE_TITLE)]
)
def test_a_stuck_file_offers_quit_now(
    qtbot: Any, stuck_parts: StuckParts, kind_case: tuple[BatchKind, str]
) -> None:
    """A file past the limit raises the prompt for its batch kind, and Quit Now exits."""
    kind, expected_title = kind_case
    controller, jobs, exit_codes = stuck_parts
    controller.enqueue_job(kind, "stuck", jobs.make("stuck"))

    box = wait_for_prompt(qtbot, controller)

    assert box.windowTitle() == expected_title
    controller.stuck_watcher.quit_now_button.click()
    assert exit_codes == [STUCK_EXIT_CODE]
    assert controller.stuck_watcher.stuck_box is None


def test_keep_waiting_restarts_the_limit(qtbot: Any, stuck_parts: StuckParts) -> None:
    """Keep Waiting closes the prompt, and the prompt returns after another full limit."""
    controller, jobs, exit_codes = stuck_parts
    controller.enqueue_job(BatchKind.ANALYZE, "stuck", jobs.make("stuck"))

    box = wait_for_prompt(qtbot, controller)
    watcher = controller.stuck_watcher
    keep_waiting = next(button for button in box.buttons() if button is not watcher.quit_now_button)
    keep_waiting.click()
    assert watcher.stuck_box is None

    wait_for_prompt(qtbot, controller)
    assert exit_codes == []


def test_the_prompt_closes_when_the_batch_ends(qtbot: Any, stuck_parts: StuckParts) -> None:
    """A batch that finishes while its prompt is open closes the prompt and stops the limit."""
    controller, jobs, exit_codes = stuck_parts
    batch = controller.enqueue_job(BatchKind.WRITE, "stuck", jobs.make("stuck"))
    wait_for_prompt(qtbot, controller)

    with qtbot.waitSignal(controller.idle, timeout=SIGNAL_TIMEOUT_MS):
        jobs.gate.set()

    assert batch.state is BatchState.COMPLETED
    assert controller.stuck_watcher.stuck_box is None
    assert not controller.stuck_watcher.timer.isActive()
    assert exit_codes == []


def test_a_cancelled_batch_still_gets_the_prompt(qtbot: Any, stuck_parts: StuckParts) -> None:
    """Cancel waits for the current file, so a hung file after Cancel still raises the prompt."""
    controller, jobs, exit_codes = stuck_parts
    batch = controller.enqueue_job(BatchKind.ANALYZE, "stuck", jobs.make("stuck"))
    qtbot.waitUntil(lambda: jobs.log == ["stuck"], timeout=SIGNAL_TIMEOUT_MS)

    controller.cancel_all()

    assert batch.cancel_requested
    wait_for_prompt(qtbot, controller)
    controller.stuck_watcher.quit_now_button.click()
    assert exit_codes == [STUCK_EXIT_CODE]


def test_a_quick_batch_raises_no_prompt(qtbot: Any, stuck_parts: StuckParts) -> None:
    """A batch that ends inside its limit never opens the prompt."""
    controller, jobs, exit_codes = stuck_parts
    jobs.gate.set()

    with qtbot.waitSignal(controller.idle, timeout=SIGNAL_TIMEOUT_MS):
        controller.enqueue_job(BatchKind.ANALYZE, "quick", jobs.make("quick"))
    qtbot.wait(QUIET_WAIT_MS)

    assert controller.stuck_watcher.stuck_box is None
    assert exit_codes == []


def test_default_limits_and_wording_follow_the_batch_kind() -> None:
    """Analysis gets 5 minutes, writes get 2, and the write prompt names the recovery."""
    five_minutes_ms = 5 * 60 * 1000
    two_minutes_ms = 2 * 60 * 1000

    assert ANALYZE_STUCK_PROMPT.timeout_ms == five_minutes_ms
    assert WRITE_STUCK_PROMPT.timeout_ms == two_minutes_ms
    assert "5 minutes" in ANALYZE_STUCK_PROMPT.text
    assert "2 minutes" in WRITE_STUCK_PROMPT.text
    assert "Recover Interrupted Writes" in WRITE_STUCK_PROMPT.text
    assert "Files written so far stay written." in WRITE_STUCK_PROMPT.text
