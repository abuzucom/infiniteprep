"""Tests for the Batch Queue strip: visibility, progress, time left, and the queue toggle."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import BatchKind
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.batch_queue import BatchQueueController
from infiniteprep.ui.batch_queue_strip import (
    ESTIMATING_TEXT,
    HIDE_QUEUE_LABEL,
    SHOW_QUEUE_LABEL,
    BatchQueueStrip,
    estimate_seconds_left,
    format_time_left,
)
from infiniteprep.ui.folder_import import FolderImportController
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.batch_jobs import GatedJobs

SIGNAL_TIMEOUT_MS = 10_000
START_TIME = 100.0
SECONDS_PER_FILE = 30.0


class StepClock:
    """A clock that returns a set time, so the time-left estimate is exact."""

    def __init__(self) -> None:
        """Start at START_TIME."""
        self.now = START_TIME

    def __call__(self) -> float:
        """Return the current set time."""
        return self.now


StripParts = tuple[BatchQueueController, BatchQueueStrip, GatedJobs, StepClock]


@pytest.fixture
def strip_parts(qtbot: Any, tmp_path: Path) -> Iterator[StripParts]:
    """Build a controller and a strip with a set clock. Teardown drains the queue.

    qtbot deletes its registered widgets before fixture teardown runs. The parent owns
    the worker thread, so the fixture deletes the parent itself after the queue drains.
    """
    parent = QWidget()
    repository = Repository(DatabaseManager(tmp_path / "queue.sqlite3"))
    import_controller = FolderImportController(
        ImportService(repository), parent, show_progress_dialog=False
    )
    controller = BatchQueueController(import_controller, repository.db.close, parent)
    clock = StepClock()
    strip = BatchQueueStrip(controller, parent, clock=clock)
    jobs = GatedJobs()
    parent.show()

    yield controller, strip, jobs, clock
    jobs.gate.set()
    qtbot.waitUntil(lambda: not controller.is_busy(), timeout=SIGNAL_TIMEOUT_MS)
    parent.close()
    parent.deleteLater()


def test_time_left_formats() -> None:
    """Time left reads in hours and minutes, minutes, or seconds."""
    assert format_time_left(3_900) == "1 h 5 min left"
    assert format_time_left(150) == "2 min left"
    assert format_time_left(40) == "40 s left"


def test_estimate_needs_a_finished_file() -> None:
    """The estimate waits for a finished file and scales the pace to the files left."""
    assert estimate_seconds_left(10.0, 0, 4) is None
    assert estimate_seconds_left(10.0, 4, 4) is None
    assert estimate_seconds_left(60.0, 2, 6) == pytest.approx(120.0)


def test_strip_hides_when_idle(qtbot: Any, strip_parts: StripParts) -> None:
    """The strip hides with nothing queued and shows while a batch runs."""
    controller, strip, jobs, _clock = strip_parts
    assert not strip.isVisible()

    controller.enqueue_job(BatchKind.ANALYZE, "Analyze 4 files", jobs.make("first"))
    qtbot.waitUntil(lambda: strip.count_label.text() == "1 of 1", timeout=SIGNAL_TIMEOUT_MS)

    assert strip.isVisible()
    assert strip.batch_label.text() == "Analyze 4 files: first"
    with qtbot.waitSignal(controller.idle, timeout=SIGNAL_TIMEOUT_MS):
        jobs.gate.set()
    assert not strip.isVisible()


def test_progress_shows_count_and_time(qtbot: Any, strip_parts: StripParts) -> None:
    """A progress report fills the bar, shows done of total, and estimates the time left."""
    controller, strip, jobs, clock = strip_parts
    batch = controller.enqueue_job(BatchKind.ANALYZE, "Analyze", jobs.make("first"))
    qtbot.waitUntil(lambda: jobs.log == ["first"], timeout=SIGNAL_TIMEOUT_MS)

    controller._on_worker_progress(0, 4, "/music/a.wav")
    first_time = strip.time_label.text()
    clock.now = START_TIME + 2 * SECONDS_PER_FILE
    controller._on_worker_progress(2, 4, "/music/c.wav")

    assert batch.progress_done == 2
    assert first_time == ESTIMATING_TEXT
    assert strip.batch_label.text() == "Analyze: c.wav"
    assert strip.count_label.text() == "2 of 4"
    assert (strip.progress_bar.value(), strip.progress_bar.maximum()) == (2, 4)
    assert strip.time_label.text() == "1 min left"


def test_click_toggles_the_queue(qtbot: Any, strip_parts: StripParts) -> None:
    """A click on the strip asks for the queue, and a second click hides it again."""
    controller, strip, jobs, _clock = strip_parts
    controller.enqueue_job(BatchKind.ANALYZE, "Analyze", jobs.make("first"))
    qtbot.waitUntil(lambda: jobs.log == ["first"], timeout=SIGNAL_TIMEOUT_MS)
    shown: list[bool] = []
    strip.queue_toggled.connect(shown.append)

    qtbot.mouseClick(strip, Qt.MouseButton.LeftButton)
    after_first = strip.queue_button.text()
    qtbot.mouseClick(strip, Qt.MouseButton.LeftButton)

    assert shown == [True, False]
    assert after_first == HIDE_QUEUE_LABEL
    assert strip.queue_button.text() == SHOW_QUEUE_LABEL


def test_quiet_sync_emits_nothing(strip_parts: StripParts) -> None:
    """Marking the queue as shown from outside relabels the button without a signal."""
    _controller, strip, _jobs, _clock = strip_parts
    shown: list[bool] = []
    strip.queue_toggled.connect(shown.append)

    strip.set_queue_shown(True)

    assert shown == []
    assert strip.queue_button.isChecked()
    assert strip.queue_button.text() == HIDE_QUEUE_LABEL


def test_window_strip_opens_the_dock(qtbot: Any, tmp_path: Path) -> None:
    """In the main window, the dock starts hidden and the strip's button shows it."""
    repository = Repository(DatabaseManager(tmp_path / "window.sqlite3"))
    window = MainWindow(
        repository, ImportService(repository, MetadataTagAdapter()), ReviewService(repository)
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    hidden_at_start = window.batch_dock.isVisible()

    window.batch_strip.queue_button.click()

    assert hidden_at_start is False
    assert window.batch_dock.isVisible()
