"""Startup dialog that resolves writes a crash or power loss left IN_PROGRESS."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.domain.models import ChangeRecord
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.action_handlers import ask_plain_question, show_plain_message
from infiniteprep.ui.time_format import format_local_timestamp

RECOVERY_DIALOG_TITLE: Final[str] = "Recover Interrupted Writes"
RECOVERY_DIALOG_WIDTH: Final[int] = 900
RECOVERY_DIALOG_HEIGHT: Final[int] = 360
RECOVERY_COLUMNS: Final[tuple[str, ...]] = ("Started", "File", "Backup", "Restore", "Keep")
COLUMN_RESTORE: Final[int] = 3
COLUMN_KEEP: Final[int] = 4
RECOVERY_INTRO: Final[str] = (
    "These writes stopped before they finished. Each file may be half-written. Restore the "
    "original from its verified backup, or keep the file as it is now. New writes stay "
    "blocked until every row is resolved."
)
MARK_FINISHED_QUESTION: Final[str] = (
    "Keep {file} as it is now?\n\n"
    "The file may be half-written. Its backup can no longer restore it after this."
)


class RecoveryDialog(QDialog):
    """Lists interrupted writes and offers a verified restore or acceptance for each."""

    def __init__(self, write_service: WriteService, parent: QWidget | None = None) -> None:
        """Build the table of interrupted writes from the WriteService journal."""
        super().__init__(parent)
        self.write_service = write_service
        self.records: list[ChangeRecord] = []
        self.setWindowTitle(RECOVERY_DIALOG_TITLE)
        self.resize(RECOVERY_DIALOG_WIDTH, RECOVERY_DIALOG_HEIGHT)
        self.table = QTableWidget(0, len(RECOVERY_COLUMNS), self)
        self.close_btn = QPushButton("Close", self)
        self._build_layout()
        self.refresh()

    def _build_layout(self) -> None:
        """Assemble the intro text, the table, and the close button."""
        root = QVBoxLayout(self)
        intro = QLabel(RECOVERY_INTRO, self)
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.table.setHorizontalHeaderLabels(list(RECOVERY_COLUMNS))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        # The rows record interrupted writes. Tab reaches the row buttons, not each cell.
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setTabKeyNavigation(False)
        self.table.setAccessibleName("Interrupted writes")
        root.addWidget(self.table, stretch=1)
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(self.close_btn)
        root.addLayout(bottom)
        self.close_btn.clicked.connect(self.reject)

    def refresh(self) -> None:
        """Reload the interrupted writes, and close the dialog once none remain."""
        self.records = self.write_service.list_interrupted_operations()
        self.table.setRowCount(len(self.records))
        for row_index, record in enumerate(self.records):
            self._populate_row(row_index, record)
        if not self.records:
            self.accept()

    def _populate_row(self, row_index: int, record: ChangeRecord) -> None:
        """Render one interrupted write with its Restore and Keep buttons."""
        cells = (
            format_local_timestamp(record.timestamp),
            Path(record.previous_path).name,
            record.backup_reference,
        )
        for column_index, text in enumerate(cells):
            self.table.setItem(row_index, column_index, QTableWidgetItem(text))
        restore_button = QPushButton("Restore from backup", self.table)
        restore_button.clicked.connect(
            lambda _c=False, operation_id=record.operation_id: self.restore(operation_id)
        )
        keep_button = QPushButton("Mark as finished", self.table)
        keep_button.clicked.connect(
            lambda _c=False, operation_id=record.operation_id: self.mark_finished(operation_id)
        )
        self.table.setCellWidget(row_index, COLUMN_RESTORE, restore_button)
        self.table.setCellWidget(row_index, COLUMN_KEEP, keep_button)

    def restore(self, operation_id: str) -> None:
        """Restore one interrupted write, or report why the backup could not be used."""
        try:
            self.write_service.restore_interrupted(operation_id)
        except (OSError, ValueError) as exc:
            show_plain_message(
                self,
                QMessageBox.Icon.Warning,
                RECOVERY_DIALOG_TITLE,
                f"Restore failed ({exc}). The write stays pending. Check the backup disk and "
                "retry, or mark the write as finished to keep the current file.",
            )
        self.refresh()

    def mark_finished(self, operation_id: str) -> None:
        """Ask first, then keep the current file for one interrupted write.

        Keeping a half-written file ends the backup restore path for that write, so it
        waits for a Yes. No is the default.
        """
        record = next(
            (
                candidate_record
                for candidate_record in self.records
                if candidate_record.operation_id == operation_id
            ),
            None,
        )
        name = Path(record.previous_path).name if record is not None else operation_id
        if not ask_plain_question(
            self, RECOVERY_DIALOG_TITLE, MARK_FINISHED_QUESTION.format(file=name)
        ):
            return
        try:
            self.write_service.mark_interrupted_finished(operation_id)
        except (OSError, ValueError) as exc:
            show_plain_message(
                self,
                QMessageBox.Icon.Warning,
                RECOVERY_DIALOG_TITLE,
                f"Could not mark the write as finished ({exc}). Check that the file exists "
                "and retry.",
            )
        self.refresh()
