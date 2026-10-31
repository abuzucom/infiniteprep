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

JOURNAL_DIALOG_TITLE: Final[str] = "Change Journal & Backup Restore"
JOURNAL_DIALOG_WIDTH: Final[int] = 960
JOURNAL_DIALOG_HEIGHT: Final[int] = 520
JOURNAL_TABLE_COLUMNS: Final[int] = 6
COL_RESTORE_ACTION: Final[int] = 5


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
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
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
        for idx, rec in enumerate(self.records):
            self._populate_record_row(idx, rec)

    def _populate_record_row(self, row_idx: int, rec: ChangeRecord) -> None:
        """Render a single ChangeRecord row with a Restore button."""
        written_str = ", ".join(f"{k}={v}" for k, v in rec.written_tags.items())
        cells = [
            rec.timestamp,
            Path(rec.previous_path).name,
            Path(rec.new_path).name,
            written_str,
            str(rec.operation_status),
        ]
        for col_idx, text in enumerate(cells):
            self.table.setItem(row_idx, col_idx, QTableWidgetItem(text))

        if rec.operation_status == OperationStatus.COMPLETED:
            btn = QPushButton("Restore Backup", self.table)
            btn.clicked.connect(
                lambda _c=False, op_id=rec.operation_id: self._restore_single(op_id)
            )
            self.table.setCellWidget(row_idx, COL_RESTORE_ACTION, btn)
        else:
            self.table.setItem(
                row_idx, COL_RESTORE_ACTION, QTableWidgetItem(str(rec.operation_status))
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
