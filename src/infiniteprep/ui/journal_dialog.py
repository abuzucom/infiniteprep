"""Change journal inspection and one-click backup restore dialog (Section 9)."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import OperationStatus
from infiniteprep.domain.models import ChangeRecord
from infiniteprep.services.write_service import WriteService

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

        self.setWindowTitle("Change Journal & Backup Restore")
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
        """Execute verified backup restore for operation_id and refresh table."""
        self.write_service.restore_operation(operation_id)
        self.refresh_journal()
