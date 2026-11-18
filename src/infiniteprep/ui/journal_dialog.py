"""Change journal inspection and one-click backup restore dialog (Section 9)."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import OperationStatus
from infiniteprep.domain.models import ChangeRecord
from infiniteprep.services.write_service import RestoreConflictError, WriteService
from infiniteprep.ui.action_handlers import ask_plain_question, show_plain_message
from infiniteprep.ui.time_format import format_local_timestamp

JOURNAL_DIALOG_TITLE: Final[str] = "Change Journal & Backup Restore"
JOURNAL_DIALOG_WIDTH: Final[int] = 960
JOURNAL_DIALOG_HEIGHT: Final[int] = 520
JOURNAL_TABLE_COLUMNS: Final[int] = 6
COLUMN_RESTORE_ACTION: Final[int] = 5


class JournalDialog(QDialog):
    """Modal dialog displaying ChangeRecords and allowing verified backup restoration."""

    def __init__(
        self,
        repository: Repository,
        write_service: WriteService,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize JournalDialog with Repository and WriteService."""
        super().__init__(parent)
        self.repo = repository
        self.write_service = write_service
        self.records: list[ChangeRecord] = []

        self.setWindowTitle(JOURNAL_DIALOG_TITLE)
        self.resize(JOURNAL_DIALOG_WIDTH, JOURNAL_DIALOG_HEIGHT)

        self.table = QTableWidget(0, JOURNAL_TABLE_COLUMNS, self)
        self.close_btn = QPushButton("Close", self)
        self._build_layout()
        self.refresh_journal()

    def _build_layout(self) -> None:
        """Assemble the dialog layout."""
        root = QVBoxLayout(self)
        self.table.setHorizontalHeaderLabels(
            [
                "Timestamp",
                "Original Path",
                "New Path",
                "Written Tags",
                "Status",
                "Restore Action",
            ]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        # The rows record past writes. Tab reaches the Restore buttons, not each cell.
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setTabKeyNavigation(False)
        self.table.setAccessibleName("Change journal")
        root.addWidget(self.table, stretch=1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(self.close_btn)
        root.addLayout(bottom)
        self.close_btn.clicked.connect(self.accept)

    def refresh_journal(self) -> None:
        """Load ChangeRecords from Repository and populate the table."""
        self.records = self.repo.list_change_records()
        self.table.setRowCount(len(self.records))
        for index, record in enumerate(self.records):
            self._populate_record_row(index, record)

    def _populate_record_row(self, row_index: int, record: ChangeRecord) -> None:
        """Render a single ChangeRecord row with a Restore button."""
        written_text = ", ".join(
            f"{tag_name}={tag_value}" for tag_name, tag_value in record.written_tags.items()
        )
        cells = [
            format_local_timestamp(record.timestamp),
            Path(record.previous_path).name,
            Path(record.new_path).name,
            written_text,
            str(record.operation_status),
        ]
        for column_index, text in enumerate(cells):
            self.table.setItem(row_index, column_index, QTableWidgetItem(text))

        if record.operation_status == OperationStatus.COMPLETED:
            restore_button = QPushButton("Restore Backup", self.table)
            restore_button.clicked.connect(
                lambda _c=False, operation_id=record.operation_id: self._restore_single(
                    operation_id
                )
            )
            self.table.setCellWidget(row_index, COLUMN_RESTORE_ACTION, restore_button)
        else:
            self.table.setItem(
                row_index, COLUMN_RESTORE_ACTION, QTableWidgetItem(str(record.operation_status))
            )

    def _restore_single(self, operation_id: str) -> None:
        """Confirm the target file, restore it from its verified backup, and refresh the table."""
        record = self.repo.get_change_record(operation_id)
        if record is None:
            return
        question = (
            f"Replace this file with its backup from before the write?\n\n{record.new_path}\n\n"
            "The current file is copied to the backup folder first."
        )
        if not ask_plain_question(self, JOURNAL_DIALOG_TITLE, question):
            return
        try:
            self._restore_with_conflict_check(operation_id)
        except (OSError, ValueError) as exc:
            show_plain_message(
                self,
                QMessageBox.Icon.Warning,
                JOURNAL_DIALOG_TITLE,
                f"Restore failed ({exc}). The current file is unchanged. Check the backup disk "
                "and retry.",
            )
        self.refresh_journal()

    def _restore_with_conflict_check(self, operation_id: str) -> None:
        """Restore, and ask again before overwriting a file edited after the write."""
        try:
            self.write_service.restore_operation(operation_id)
        except RestoreConflictError as conflict:
            if ask_plain_question(self, JOURNAL_DIALOG_TITLE, f"{conflict}\n\nRestore anyway?"):
                self.write_service.restore_operation(operation_id, allow_modified=True)
