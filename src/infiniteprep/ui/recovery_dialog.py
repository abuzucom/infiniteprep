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
from infiniteprep.ui.action_handlers import show_plain_message

RECOVERY_DIALOG_TITLE: Final[str] = "Recover Interrupted Writes"
RECOVERY_DIALOG_WIDTH: Final[int] = 900
RECOVERY_DIALOG_HEIGHT: Final[int] = 360
RECOVERY_COLUMNS: Final[tuple[str, ...]] = ("Started", "File", "Backup", "Restore", "Keep")
COL_RESTORE: Final[int] = 3
COL_KEEP: Final[int] = 4
RECOVERY_INTRO: Final[str] = (
    "These writes stopped before they finished. Each file may be half-written. Restore the "
    "original from its verified backup, or keep the file as it is now. New writes stay "
    "blocked until every row is resolved."
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
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
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
        for row_idx, record in enumerate(self.records):
            self._populate_row(row_idx, record)
        if not self.records:
            self.accept()

    def _populate_row(self, row_idx: int, record: ChangeRecord) -> None:
        """Render one interrupted write with its Restore and Keep buttons."""
        cells = (record.timestamp, Path(record.previous_path).name, record.backup_reference)
        for col_idx, text in enumerate(cells):
            self.table.setItem(row_idx, col_idx, QTableWidgetItem(text))
        restore_btn = QPushButton("Restore from backup", self.table)
        restore_btn.clicked.connect(
            lambda _c=False, op_id=record.operation_id: self.restore(op_id)
        )
        keep_btn = QPushButton("Mark as finished", self.table)
        keep_btn.clicked.connect(
            lambda _c=False, op_id=record.operation_id: self.mark_finished(op_id)
        )
        self.table.setCellWidget(row_idx, COL_RESTORE, restore_btn)
        self.table.setCellWidget(row_idx, COL_KEEP, keep_btn)

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
        """Keep the current file for one interrupted write, or report why that failed."""
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
