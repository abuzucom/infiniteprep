"""Main application window for InfinitePrep (Section 9)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    FieldName,
    KeyDisplayNotation,
    format_canonical_key,
    parse_key_string,
)
from infiniteprep.domain.models import FileAsset
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.detail_panel import DetailPanel

TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Filename",
    "Format",
    "Duration",
    "Artist (Cur -> Prop)",
    "Title (Cur -> Prop)",
    "Mix / Version",
    "Label / Cat#",
    "BPM",
    "Key",
    "Review Status",
    "Hardware / Rip Warnings",
)
MAIN_WINDOW_WIDTH: Final[int] = 1360
MAIN_WINDOW_HEIGHT: Final[int] = 820
SEARCH_INPUT_STRETCH: Final[int] = 3
STATUS_FILTER_STRETCH: Final[int] = 1
SPLITTER_TOP_HEIGHT: Final[int] = 380
SPLITTER_BOTTOM_HEIGHT: Final[int] = 420


class MainWindow(QMainWindow):
    """Primary desktop window with batch file table, filter bar, toolbar, and detail panel."""

    def __init__(
        self,
        repository: Repository,
        import_service: ImportService,
        review_service: ReviewService,
        *,
        on_analyze_batch: Callable[[list[str]], None] | None = None,
        on_open_write_dialog: Callable[[list[str]], None] | None = None,
        on_open_journal_dialog: Callable[[], None] | None = None,
        on_open_settings_dialog: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        on_transcode_batch: Callable[[list[str]], object] | None = None,
    ) -> None:
        """Initialize MainWindow with core services and callbacks."""
        super().__init__(parent)
        self.repo = repository
        self.import_service = import_service
        self.review_service = review_service
        self.on_analyze_batch = on_analyze_batch
        self.on_open_write_dialog = on_open_write_dialog
        self.on_open_journal_dialog = on_open_journal_dialog
        self.on_open_settings_dialog = on_open_settings_dialog
        self.on_transcode_batch = on_transcode_batch

        self.current_key_notation: KeyDisplayNotation = KeyDisplayNotation.CAMELOT
        self.visible_file_ids: list[str] = []

        self.setWindowTitle("InfinitePrep - DJ Music Metadata & Audio Preparation")
        self.resize(MAIN_WINDOW_WIDTH, MAIN_WINDOW_HEIGHT)

        self.search_input = QLineEdit(self)
        self.status_filter_combo = QComboBox(self)
        self.key_notation_combo = QComboBox(self)
        self.file_table = QTableWidget(0, len(TABLE_COLUMNS), self)
        self.detail_panel = DetailPanel(
            repository=self.repo,
            review_service=self.review_service,
            on_change_callback=self.refresh_file_table,
            parent=self,
        )

        self._setup_toolbar()
        self._setup_central_widget()
        self.refresh_file_table()

    def _setup_toolbar(self) -> None:
        """Build top action toolbar with import, bulk approve, key notation, and dialogs."""
        toolbar = QToolBar("Main Actions", self)
        self.addToolBar(toolbar)

        import_btn = QPushButton("Import Folder", self)
        import_btn.clicked.connect(self._prompt_import_folder)
        toolbar.addWidget(import_btn)

        analyze_btn = QPushButton("Identify & Analyze All", self)
        analyze_btn.clicked.connect(self._trigger_analyze_all)
        toolbar.addWidget(analyze_btn)

        bulk_btn = QPushButton("Bulk Approve Non-Conflicting", self)
        bulk_btn.clicked.connect(self._trigger_bulk_approve)
        toolbar.addWidget(bulk_btn)

        write_btn = QPushButton("Preview & Apply Writes", self)
        write_btn.clicked.connect(self._trigger_open_write_dialog)
        toolbar.addWidget(write_btn)

        self.transcode_btn = QPushButton("Transcode to XDJ", self)
        self.transcode_btn.clicked.connect(self._trigger_transcode_batch)
        toolbar.addWidget(self.transcode_btn)

        journal_btn = QPushButton("Change Journal & Restore", self)
        journal_btn.clicked.connect(self._trigger_open_journal)
        toolbar.addWidget(journal_btn)

        settings_btn = QPushButton("Settings & Providers", self)
        settings_btn.clicked.connect(self._trigger_open_settings)
        toolbar.addWidget(settings_btn)

        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Key Notation: ", self))
        for notation in KeyDisplayNotation:
            self.key_notation_combo.addItem(notation.value)
        self.key_notation_combo.setCurrentText(self.current_key_notation.value)
        self.key_notation_combo.currentTextChanged.connect(self._on_key_notation_changed)
        toolbar.addWidget(self.key_notation_combo)

    def _setup_central_widget(self) -> None:
        """Build filter bar, file table, and vertical/horizontal splitter with DetailPanel."""
        central = QWidget(self)
        root_layout = QVBoxLayout(central)

        filter_bar = QHBoxLayout()
        self.search_input.setPlaceholderText(
            "Filter by filename, artist, title, label, or warning..."
        )
        self.search_input.textChanged.connect(self.refresh_file_table)
        filter_bar.addWidget(self.search_input, stretch=SEARCH_INPUT_STRETCH)

        self.status_filter_combo.addItems(
            [
                "All Files",
                "Has Warnings (XDJ-RX3 / Rip)",
                "Unprocessed",
                "Proposed",
                "Partially Approved",
                "Ready to Write",
                "Written",
                "Error",
            ]
        )
        self.status_filter_combo.currentTextChanged.connect(self.refresh_file_table)
        filter_bar.addWidget(self.status_filter_combo, stretch=STATUS_FILTER_STRETCH)
        root_layout.addLayout(filter_bar)

        self.file_table.setHorizontalHeaderLabels(list(TABLE_COLUMNS))
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.itemSelectionChanged.connect(self._on_table_selection_changed)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self.file_table)
        splitter.addWidget(self.detail_panel)
        splitter.setSizes([SPLITTER_TOP_HEIGHT, SPLITTER_BOTTOM_HEIGHT])
        root_layout.addWidget(splitter)
        self.setCentralWidget(central)

    def refresh_file_table(self) -> None:
        """Reload all FileAssets from Repository and populate the filtered table."""
        all_files = self.repo.list_files()
        filtered = [f for f in all_files if self._matches_active_filters(f)]
        self.visible_file_ids = [f.id for f in filtered]

        self.file_table.blockSignals(True)
        self.file_table.setRowCount(len(filtered))
        for row_idx, asset in enumerate(filtered):
            self._populate_table_row(row_idx, asset)
        self.file_table.blockSignals(False)

    def _matches_active_filters(self, asset: FileAsset) -> bool:
        """Return True if asset matches the search query and status filter dropdown."""
        status_choice = self.status_filter_combo.currentText()
        tech_warnings = asset.original_tags.get("technical", {}).get("warnings", [])
        if status_choice == "Has Warnings (XDJ-RX3 / Rip)" and not tech_warnings:
            return False
        status_map = {
            "Unprocessed": "unprocessed",
            "Proposed": "proposed",
            "Partially Approved": "partially_approved",
            "Ready to Write": "ready_to_write",
            "Written": "written",
            "Error": "error",
        }
        if status_choice in status_map and str(asset.review_status) != status_map[status_choice]:
            return False

        query = self.search_input.text().strip().lower()
        if not query:
            return True
        haystack = f"{asset.current_path} {asset.original_tags}".lower()
        return query in haystack

    def _populate_table_row(self, row_idx: int, asset: FileAsset) -> None:
        """Render a single FileAsset row in the batch review table."""
        cells = self._build_table_row_cells(asset)
        for col_idx, text in enumerate(cells):
            self.file_table.setItem(row_idx, col_idx, QTableWidgetItem(text))

    def _build_table_row_cells(self, asset: FileAsset) -> list[str]:
        """Compute string cells for a single FileAsset row in the batch review table."""
        std = asset.original_tags.get("standard_tags", {})
        approved = self.repo.get_approved_fields_map(asset.id)
        top_by_field: dict[str, str] = {}
        for p in self.repo.list_field_proposals(asset.id):
            top_by_field.setdefault(p.field_name, p.proposed_value)

        lbl_str = self._effective_val(std, approved, top_by_field, FieldName.LABEL.value)
        cat_str = self._effective_val(std, approved, top_by_field, FieldName.CATALOG_NUMBER.value)
        raw_key = self._effective_val(std, approved, top_by_field, FieldName.KEY.value)
        warnings = asset.original_tags.get("technical", {}).get("warnings", [])

        return [
            Path(asset.current_path).name,
            str(asset.format).upper(),
            f"{asset.duration:.1f}s" if asset.duration is not None else "-",
            self._summary_pair(std, approved, top_by_field, FieldName.ARTIST.value),
            self._summary_pair(std, approved, top_by_field, FieldName.TITLE.value),
            self._effective_val(std, approved, top_by_field, FieldName.MIX_NAME.value),
            f"{lbl_str} [{cat_str}]" if cat_str else lbl_str,
            self._effective_val(std, approved, top_by_field, FieldName.BPM.value),
            self._format_key_cell(raw_key),
            str(asset.review_status),
            "; ".join(warnings) if warnings else "OK",
        ]

    def _summary_pair(
        self,
        std: dict[str, str],
        approved: dict,
        top_by_field: dict[str, str],
        field_name: str,
    ) -> str:
        """Format 'Current -> Approved/Proposed' summary string for table cells."""
        cur = std.get(field_name, "-")
        target = (
            approved[field_name].approved_value
            if field_name in approved
            else top_by_field.get(field_name, "")
        )
        if target and target != cur:
            return f"{cur} -> {target}"
        return target or cur

    def _effective_val(
        self,
        std: dict[str, str],
        approved: dict,
        top_by_field: dict[str, str],
        field_name: str,
    ) -> str:
        """Return approved value, top proposal, or existing standard tag."""
        if field_name in approved:
            return approved[field_name].approved_value
        return top_by_field.get(field_name) or std.get(field_name, "")

    def _format_key_cell(self, raw_key: str) -> str:
        """Format musical key according to active KeyDisplayNotation."""
        parsed = parse_key_string(raw_key)
        if parsed is None:
            return raw_key
        return format_canonical_key(parsed[0], parsed[1], self.current_key_notation)

    def _on_table_selection_changed(self) -> None:
        """Load selected file into DetailPanel when table selection changes."""
        selected_rows = self.file_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        row_idx = selected_rows[0].row()
        if 0 <= row_idx < len(self.visible_file_ids):
            self.detail_panel.load_file_asset(self.visible_file_ids[row_idx])

    def _on_key_notation_changed(self, text: str) -> None:
        """Update key notation across table and DetailPanel."""
        for notation in KeyDisplayNotation:
            if notation.value == text:
                self.current_key_notation = notation
                break
        self.detail_panel.set_key_notation(self.current_key_notation)
        self.refresh_file_table()

    def _prompt_import_folder(self) -> None:
        """Open directory picker dialog and run non-destructive import."""
        chosen = QFileDialog.getExistingDirectory(self, "Select Music Folder to Import")
        if chosen:
            self.import_service.import_paths([Path(chosen)])
            self.refresh_file_table()

    def _trigger_analyze_all(self) -> None:
        """Invoke batch identification and analysis callback on visible files."""
        if self.on_analyze_batch and self.visible_file_ids:
            self.on_analyze_batch(list(self.visible_file_ids))
            self.refresh_file_table()

    def _trigger_bulk_approve(self) -> None:
        """Bulk approve non-conflicting proposals for all visible files."""
        if self.visible_file_ids:
            self.review_service.bulk_approve_non_conflicting(self.visible_file_ids)
            self.refresh_file_table()
            if self.detail_panel.current_file_id:
                self.detail_panel.load_file_asset(self.detail_panel.current_file_id)

    def _trigger_open_write_dialog(self) -> None:
        """Open write preview and execution dialog if callback is registered."""
        if self.on_open_write_dialog:
            self.on_open_write_dialog(list(self.visible_file_ids))

    def _trigger_transcode_batch(self) -> None:
        """Invoke the batch transcode callback on visible files and refresh the table."""
        if self.on_transcode_batch and self.visible_file_ids:
            self.on_transcode_batch(list(self.visible_file_ids))
            self.refresh_file_table()

    def _trigger_open_journal(self) -> None:
        """Open change journal and restore dialog if callback is registered."""
        if self.on_open_journal_dialog:
            self.on_open_journal_dialog()

    def _trigger_open_settings(self) -> None:
        """Open provider and setup settings dialog if callback is registered."""
        if self.on_open_settings_dialog:
            self.on_open_settings_dialog()
