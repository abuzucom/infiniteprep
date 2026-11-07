"""Background folder import: worker thread, modal progress dialog, and end-of-batch report."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.services.import_service import (
    ImportBatchReport,
    ImportOutcome,
    ImportService,
)

logger = logging.getLogger(__name__)

REPORT_DIALOG_WIDTH: Final[int] = 720
REPORT_DIALOG_HEIGHT: Final[int] = 480
CANCELLING_TEXT: Final[str] = "Cancelling after the current file. Nothing will be imported."
# One file (or the scan) running longer than this counts as stuck (D244).
STUCK_FILE_TIMEOUT_MS: Final[int] = 120_000
STUCK_EXIT_CODE: Final[int] = 1
STUCK_TEXT: Final[str] = (
    "One file has taken more than 2 minutes and the import is not responding. "
    "Quit Now ends InfinitePrep at once and keeps nothing from this import. "
    "Keep Waiting lets the import continue."
)


def _report_sections(report: ImportBatchReport) -> list[tuple[str, list[str]]]:
    """Return each report category with one line per file."""
    duplicates = [
        f"{asset.current_path} (same content as {', '.join(p.current_path for p in prior)})"
        for asset, prior in report.possible_duplicates
    ]
    return [
        ("Imported", [asset.current_path for asset in report.imported_files]),
        ("Already imported", list(report.previously_imported_paths)),
        ("Unsupported", list(report.unsupported_files)),
        ("Damaged", [f"{path}: {reason}" for path, reason in report.damaged_files]),
        ("Possible duplicates", duplicates),
        ("Failed", [f"{path}: {reason}" for path, reason in report.failed_files]),
    ]


def make_plain_label(text: str, parent: QWidget | None = None) -> QLabel:
    """Return a label that shows text literally. File names never render as rich text."""
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _report_headline(report: ImportBatchReport) -> str:
    """Return the one-line result of the batch."""
    if report.outcome is ImportOutcome.CANCELLED:
        return "Cancelled. No file was imported."
    return f"Import finished. Scanned {report.scanned_count} files."


def format_import_report(report: ImportBatchReport) -> str:
    """Render the report as plain text: the result, a count per category, then each file."""
    sections = _report_sections(report)
    lines = [_report_headline(report), ""]
    lines.extend(f"{title}: {len(entries)}" for title, entries in sections)
    for title, entries in sections:
        if entries:
            lines.extend(["", f"{title}:"])
            lines.extend(f"  {entry}" for entry in entries)
    return "\n".join(lines)


class ImportReportDialog(QDialog):
    """Show the batch result, a count per category, and expandable per-file lists."""

    def __init__(self, report: ImportBatchReport, parent: QWidget | None = None) -> None:
        """Build the dialog from a finished ImportBatchReport."""
        super().__init__(parent)
        self.setWindowTitle("Import Report")
        self.resize(REPORT_DIALOG_WIDTH, REPORT_DIALOG_HEIGHT)
        self.report_text = format_import_report(report)
        layout = QVBoxLayout(self)
        self.headline_label = make_plain_label(_report_headline(report), self)
        layout.addWidget(self.headline_label)
        self.sections_tree = QTreeWidget(self)
        self.sections_tree.setHeaderHidden(True)
        for title, entries in _report_sections(report):
            section = QTreeWidgetItem([f"{title}: {len(entries)}"])
            section.addChildren([QTreeWidgetItem([entry]) for entry in entries])
            self.sections_tree.addTopLevelItem(section)
        layout.addWidget(self.sections_tree)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.copy_button = QPushButton("Copy", self)
        buttons.addButton(self.copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.copy_button.clicked.connect(self._copy_report)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _copy_report(self) -> None:
        """Put the plain-text report on the clipboard."""
        QApplication.clipboard().setText(self.report_text)


class ImportWorker(QObject):
    """Run one ImportService batch on a worker thread and report through signals."""

    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, import_service: ImportService, paths: list[Path]) -> None:
        """Hold the service and paths. run() does the work on the worker thread."""
        super().__init__()
        self.import_service = import_service
        self.paths = paths
        self._cancel_requested = threading.Event()

    def request_cancel(self) -> None:
        """Ask the batch to stop before its next file and roll back."""
        self._cancel_requested.set()

    @Slot()
    def run(self) -> None:
        """Import the paths, then emit finished with the report or failed with the error.

        The worker closes its own SQLite connection before either signal, so the UI
        thread's join after the signal waits on nothing.
        """
        report: ImportBatchReport | None = None
        error_text = ""
        try:
            report = self.import_service.import_paths(
                list(self.paths),
                progress=self.progress.emit,
                should_cancel=self._cancel_requested.is_set,
            )
        # Any exception here would otherwise end the thread silently. The batch has
        # already rolled back, so the UI reports the error and stays usable.
        except Exception as exc:
            logger.exception("Folder import failed and rolled back.")
            error_text = f"{type(exc).__name__}: {exc}"
        finally:
            # DatabaseManager keeps one connection per thread. This closes the worker's.
            self.import_service.repo.db.close()
        if report is None:
            self.failed.emit(error_text)
        else:
            self.finished.emit(report)


class FolderImportController(QObject):
    """Start one background import at a time and show its modal progress dialog.

    completed carries the ImportBatchReport, or None when the batch failed.
    """

    completed = Signal(object)

    def __init__(
        self,
        import_service: ImportService,
        parent: QWidget,
        *,
        stuck_timeout_ms: int = STUCK_FILE_TIMEOUT_MS,
        exit_process: Callable[[int], object] = os._exit,
    ) -> None:
        """Keep the service and the window that owns the progress dialog.

        A file that runs past stuck_timeout_ms raises the stuck prompt. Quit Now calls
        exit_process, which ends the process at once. SQLite discards the open batch.
        """
        super().__init__(parent)
        self.import_service = import_service
        self.parent_widget = parent
        self._thread: QThread | None = None
        self._worker: ImportWorker | None = None
        self._dialog: QProgressDialog | None = None
        self.progress_label: QLabel | None = None
        self.stuck_box: QMessageBox | None = None
        self.quit_now_button: QAbstractButton | None = None
        self.last_error = ""
        self._exit_process = exit_process
        self._stuck_timer = QTimer(self)
        self._stuck_timer.setSingleShot(True)
        self._stuck_timer.setInterval(stuck_timeout_ms)
        self._stuck_timer.timeout.connect(self._on_file_stuck)

    def is_running(self) -> bool:
        """Return True while a batch is in progress."""
        return self._thread is not None

    def start(self, paths: list[Path]) -> bool:
        """Start a batch for paths. Return False when another batch is still running."""
        if self.is_running():
            return False
        self._worker = ImportWorker(self.import_service, paths)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._dialog = self._build_progress_dialog()
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()
        self._dialog.show()
        self._stuck_timer.start()
        return True

    def request_cancel(self) -> None:
        """Ask the running batch to stop after the current file and roll back."""
        if self._worker is not None:
            self._worker.request_cancel()
        if self._dialog is not None:
            self._dialog.setLabelText(CANCELLING_TEXT)

    def _build_progress_dialog(self) -> QProgressDialog:
        """Create the window-modal progress dialog with a Cancel button."""
        dialog = QProgressDialog("Scanning folder...", "Cancel", 0, 0, self.parent_widget)
        # The label shows file paths, so it stays plain text.
        self.progress_label = make_plain_label("Scanning folder...", dialog)
        dialog.setLabel(self.progress_label)
        dialog.setWindowTitle("Importing")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(self.request_cancel)
        return dialog

    @Slot(int, int, str)
    def _on_progress(self, done: int, total: int, path: str) -> None:
        """Show the current file and its number in the batch, and restart the stuck timer."""
        self._stuck_timer.start()
        dialog = self._dialog
        if dialog is None or dialog.wasCanceled():
            return
        # setValue on a modal dialog processes pending events. A queued finish can run
        # _finish inside it and clear self._dialog, so setValue goes last.
        dialog.setLabelText(f"Importing {done} of {total}:\n{path}")
        dialog.setMaximum(total)
        dialog.setValue(done - 1)

    @Slot()
    def _on_file_stuck(self) -> None:
        """Offer Quit Now or Keep Waiting after one file runs past the time limit."""
        if not self.is_running() or self.stuck_box is not None:
            return
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            "Import Not Responding",
            STUCK_TEXT,
            QMessageBox.StandardButton.NoButton,
            self.parent_widget,
        )
        box.setTextFormat(Qt.TextFormat.PlainText)
        self.quit_now_button = box.addButton("Quit Now", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Keep Waiting", QMessageBox.ButtonRole.RejectRole)
        box.buttonClicked.connect(self._on_stuck_choice)
        self.stuck_box = box
        box.open()

    @Slot(QAbstractButton)
    def _on_stuck_choice(self, button: QAbstractButton) -> None:
        """End the process at once, or wait another full time limit."""
        quit_now = button is self.quit_now_button
        self._close_stuck_box()
        if quit_now:
            logger.warning("Import stuck past the time limit. Quit Now ends the process.")
            self._exit_process(STUCK_EXIT_CODE)
            return
        if self.is_running():
            self._stuck_timer.start()

    def _close_stuck_box(self) -> None:
        """Close the stuck prompt if it is open."""
        box, self.stuck_box = self.stuck_box, None
        if box is not None:
            box.close()
            box.deleteLater()

    @Slot(object)
    def _on_finished(self, report: ImportBatchReport) -> None:
        """Join the worker thread and hand the report on."""
        self._finish()
        self.completed.emit(report)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        """Join the worker thread and report the failure."""
        self.last_error = message
        self._finish()
        self.completed.emit(None)

    def _finish(self) -> None:
        """Stop the worker thread, wait for it, and close the progress dialog.

        The references clear first. Closing a QProgressDialog emits canceled, and
        request_cancel then finds no batch to cancel.
        """
        self._stuck_timer.stop()
        self._close_stuck_box()
        thread, worker, dialog = self._thread, self._worker, self._dialog
        self._thread = None
        self._worker = None
        self._dialog = None
        if thread is not None:
            thread.quit()
            thread.wait()
            thread.deleteLater()
        if worker is not None:
            worker.deleteLater()
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
