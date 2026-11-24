"""History: past tag writes on the left, the chosen write's changes and Restore on the right."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.models import ChangeRecord
from infiniteprep.services.write_service import RestoreConflictError, WriteService
from infiniteprep.ui.action_handlers import ask_plain_question, show_plain_message
from infiniteprep.ui.detail_panel import field_display_name
from infiniteprep.ui.history_model import (
    NO_SELECTION_TEXT,
    backup_state_text,
    can_restore,
    history_status_label,
    record_field_diff,
)
from infiniteprep.ui.time_format import format_local_timestamp

JOURNAL_DIALOG_TITLE: Final[str] = "History"
JOURNAL_DIALOG_WIDTH: Final[int] = 960
JOURNAL_DIALOG_HEIGHT: Final[int] = 520
JOURNAL_LIST_HEADERS: Final[tuple[str, ...]] = ("When", "File", "Status")
JOURNAL_TABLE_COLUMNS: Final[int] = len(JOURNAL_LIST_HEADERS)
COLUMN_WHEN: Final[int] = 0
COLUMN_FILE: Final[int] = 1
COLUMN_STATUS: Final[int] = 2
DIFF_HEADERS: Final[tuple[str, ...]] = ("Field", "Before", "After")
RESTORE_LABEL: Final[str] = "Restore"
RENAME_ARROW: Final[str] = " -> "
# The list takes the left share of the dialog. The detail side takes the rest.
LIST_STRETCH: Final[int] = 2
DETAIL_STRETCH: Final[int] = 3
HISTORY_LIST_NAME: Final[str] = "History"


def format_restore_failure(reason: object) -> str:
    """Return the message for a restore that failed, with the reason it gave."""
    return (
        f"Couldn't restore this file ({reason}). It's unchanged. "
        "Check the backup drive and try again."
    )


class JournalDialog(QDialog):
    """Show past tag writes and restore a chosen one from its verified backup."""

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
        self.detail_title = QLabel(NO_SELECTION_TEXT, self)
        self.detail_title.setTextFormat(Qt.TextFormat.PlainText)
        self.diff_table = QTableWidget(0, len(DIFF_HEADERS), self)
        self.backup_label = QLabel("", self)
        self.backup_label.setWordWrap(True)
        self.restore_btn = QPushButton(RESTORE_LABEL, self)
        self.restore_btn.setEnabled(False)
        self.close_btn = QPushButton("Close", self)
        self._build_layout()
        self.refresh_journal()

    def _build_layout(self) -> None:
        """Place the write list beside the detail side, above the Close button."""
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._setup_table(self.table, JOURNAL_LIST_HEADERS, HISTORY_LIST_NAME)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._show_selected_record)
        splitter.addWidget(self.table)

        detail = QWidget(splitter)
        detail_layout = QVBoxLayout(detail)
        detail_layout.addWidget(self.detail_title)
        self._setup_table(self.diff_table, DIFF_HEADERS, "Fields this change wrote")
        detail_layout.addWidget(self.diff_table, stretch=1)
        detail_layout.addWidget(self.backup_label)
        restore_row = QHBoxLayout()
        restore_row.addStretch()
        restore_row.addWidget(self.restore_btn)
        detail_layout.addLayout(restore_row)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, LIST_STRETCH)
        splitter.setStretchFactor(1, DETAIL_STRETCH)
        root.addWidget(splitter, stretch=1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(self.close_btn)
        root.addLayout(bottom)
        self.restore_btn.clicked.connect(self._restore_selected)
        self.close_btn.clicked.connect(self.accept)

    @staticmethod
    def _setup_table(table: QTableWidget, headers: tuple[str, ...], name: str) -> None:
        """Make a read-only table that Tab leaves in one press."""
        table.setHorizontalHeaderLabels(list(headers))
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        # The rows record past writes. An edit in a cell changed nothing and was lost.
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setTabKeyNavigation(False)
        table.setAccessibleName(name)

    def refresh_journal(self) -> None:
        """Load the journal, newest first, and show the first write's details."""
        self.records = self.repo.list_change_records()
        self.table.setRowCount(len(self.records))
        for index, record in enumerate(self.records):
            self._populate_record_row(index, record)
        if self.records:
            self.table.selectRow(0)
        self._show_selected_record()

    def _populate_record_row(self, row_index: int, record: ChangeRecord) -> None:
        """Render one write: when it ran, the file, and its status."""
        cells = [
            format_local_timestamp(record.timestamp),
            Path(record.new_path).name,
            history_status_label(str(record.operation_status)),
        ]
        for column_index, text in enumerate(cells):
            self.table.setItem(row_index, column_index, QTableWidgetItem(text))

    def selected_record(self) -> ChangeRecord | None:
        """Return the write the list selects, or None."""
        rows = self.table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self.records):
            return None
        return self.records[rows[0].row()]

    def _show_selected_record(self) -> None:
        """Show the selected write's fields, its backup state, and the Restore button."""
        record = self.selected_record()
        if record is None:
            self.detail_title.setText(NO_SELECTION_TEXT)
            self.diff_table.setRowCount(0)
            self.backup_label.clear()
            self.restore_btn.setEnabled(False)
            return
        old_name, new_name = Path(record.previous_path).name, Path(record.new_path).name
        title = new_name if old_name == new_name else f"{old_name}{RENAME_ARROW}{new_name}"
        self.detail_title.setText(title)
        diff = record_field_diff(record, field_display_name)
        self.diff_table.setRowCount(len(diff))
        for row_index, row in enumerate(diff):
            for column_index, text in enumerate(row):
                self.diff_table.setItem(row_index, column_index, QTableWidgetItem(text))
        self.backup_label.setText(backup_state_text(record))
        self.restore_btn.setEnabled(can_restore(record))

    def _restore_selected(self) -> None:
        """Restore the selected write from its backup."""
        record = self.selected_record()
        if record is not None and can_restore(record):
            self._restore_single(record.operation_id)

    def _restore_single(self, operation_id: str) -> None:
        """Confirm the target file, restore it from its verified backup, and refresh the list."""
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
                format_restore_failure(exc),
            )
        self.refresh_journal()

    def _restore_with_conflict_check(self, operation_id: str) -> None:
        """Restore, and ask again before overwriting a file edited after the write."""
        try:
            self.write_service.restore_operation(operation_id)
        except RestoreConflictError as conflict:
            if ask_plain_question(self, JOURNAL_DIALOG_TITLE, f"{conflict}\n\nRestore anyway?"):
                self.write_service.restore_operation(operation_id, allow_modified=True)
