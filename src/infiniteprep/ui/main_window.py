"""Main application window for InfinitePrep (Section 9)."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from pathlib import Path
from typing import Final

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.config.settings import SettingsManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    FieldName,
    KeyDisplayNotation,
    format_canonical_key,
    parse_key_string,
)
from infiniteprep.domain.models import FileAsset
from infiniteprep.services.import_service import ImportBatchReport, ImportService
from infiniteprep.services.job_orchestrator import BatchKind, BatchState, QueuedBatch
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.batch_queue import (
    BatchQueueController,
    BatchRun,
    CancelCheck,
    ProgressReport,
    format_batch_result,
)
from infiniteprep.ui.batch_queue_dock import BatchQueueDock
from infiniteprep.ui.detail_panel import DetailPanel
from infiniteprep.ui.folder_import import FolderImportController, ImportReportDialog
from infiniteprep.ui.theme import DEFAULT_DARK_THEME, apply_application_theme, get_available_themes
from infiniteprep.ui.time_format import split_time_seconds

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
PLAYLIST_SUFFIX: Final[str] = ".m3u8"
CLOSE_WAIT: Final[str] = "wait"
CLOSE_CANCEL: Final[str] = "cancel"
CLOSE_STAY: Final[str] = "stay"
IMPORT_FAILURE_TEXT: Final[str] = (
    "The import stopped and nothing was imported. Check the folder and try again."
)
CLOSE_PROMPT_TEXT: Final[str] = (
    "A batch is still running or waiting. Keep working, or cancel and exit. "
    "Cancel stops the running batch after its current file and drops every waiting batch. "
    "A cancelled import keeps nothing."
)
# Runs a queued Analyze batch on a worker thread: file IDs, cancel check, progress report.
AnalyzeRunner = Callable[[list[str], CancelCheck, ProgressReport], object]


def format_duration(seconds: float) -> str:
    """Format a track duration as M:SS or H:MM:SS, truncating fractions."""
    whole_seconds = int(max(0.0, float(seconds)))
    hours, minutes, seconds_part = split_time_seconds(whole_seconds)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds_part:02d}"
    return f"{minutes}:{seconds_part:02d}"


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
        on_export_playlist: Callable[[list[str], Path], object] | None = None,
        enable_audio_backend: bool = False,
        analyze_runner: AnalyzeRunner | None = None,
    ) -> None:
        """Initialize MainWindow with core services and callbacks.

        enable_audio_backend turns on real audio playback in the detail panel preview.
        analyze_runner runs Identify & Analyze as a queued batch on a worker thread.
        Without it, on_analyze_batch runs on the calling thread.
        """
        super().__init__(parent)
        self.repo = repository
        self.import_service = import_service
        self.review_service = review_service
        self.on_analyze_batch = on_analyze_batch
        self.analyze_runner = analyze_runner
        self.on_open_write_dialog = on_open_write_dialog
        self.on_open_journal_dialog = on_open_journal_dialog
        self.on_open_settings_dialog = on_open_settings_dialog
        self.on_transcode_batch = on_transcode_batch
        self.on_export_playlist = on_export_playlist

        self.current_key_notation: KeyDisplayNotation = KeyDisplayNotation.CAMELOT
        self.visible_file_ids: list[str] = []

        self.setWindowTitle("InfinitePrep - DJ Music Metadata & Audio Preparation")
        self.resize(MAIN_WINDOW_WIDTH, MAIN_WINDOW_HEIGHT)

        self.search_input = QLineEdit(self)
        self.status_filter_combo = QComboBox(self)
        self.key_notation_combo = QComboBox(self)
        self.theme_combo = QComboBox(self)
        self.file_table = QTableWidget(0, len(TABLE_COLUMNS), self)
        self.detail_panel = DetailPanel(
            repository=self.repo,
            review_service=self.review_service,
            on_change_callback=self.refresh_file_table,
            parent=self,
            enable_audio_backend=enable_audio_backend,
        )
        self._setup_toolbar()
        self._setup_central_widget()
        self._setup_batch_queue()
        self._restore_table_layouts()
        self.refresh_file_table()

    def _setup_batch_queue(self) -> None:
        """Create the batch queue, its dock panel, and the import controller it drives."""
        self.import_controller = FolderImportController(
            self.import_service, self, show_progress_dialog=False
        )
        self.batch_queue = BatchQueueController(self.import_controller, self.repo.db.close, self)
        self.batch_dock = BatchQueueDock(self.batch_queue, self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.batch_dock)
        self._close_after_queue = False
        self.import_failure_box: QMessageBox | None = None
        self.batch_result_box: QMessageBox | None = None
        self.batch_queue.batch_finished.connect(self._on_batch_finished)
        self.batch_queue.changed.connect(self._update_action_locks)
        self.batch_queue.idle.connect(self._on_queue_idle)
        self.batch_dock.result_requested.connect(self.show_batch_result)

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

        self.bulk_btn = QPushButton("Bulk Approve Non-Conflicting", self)
        self.bulk_btn.clicked.connect(self._trigger_bulk_approve)
        toolbar.addWidget(self.bulk_btn)

        write_btn = QPushButton("Preview & Apply Writes", self)
        write_btn.clicked.connect(self._trigger_open_write_dialog)
        toolbar.addWidget(write_btn)

        self.transcode_btn = QPushButton("Transcode to XDJ", self)
        self.transcode_btn.clicked.connect(self._trigger_transcode_batch)
        toolbar.addWidget(self.transcode_btn)

        self.export_playlist_btn = QPushButton("Export Playlist (.m3u8)", self)
        self.export_playlist_btn.clicked.connect(self._trigger_export_playlist)
        toolbar.addWidget(self.export_playlist_btn)

        self.journal_btn = QPushButton("Change Journal & Restore", self)
        self.journal_btn.clicked.connect(self._trigger_open_journal)
        toolbar.addWidget(self.journal_btn)

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

        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Theme: ", self))
        for theme_name in get_available_themes():
            self.theme_combo.addItem(theme_name)
        self.theme_combo.addItem("system")
        current_theme = getattr(SettingsManager().load(), "theme", DEFAULT_DARK_THEME)
        self.theme_combo.setCurrentText(current_theme)
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        toolbar.addWidget(self.theme_combo)

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
        file_header = self.file_table.horizontalHeader()
        file_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        file_header.setStretchLastSection(True)
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.itemSelectionChanged.connect(self._on_table_selection_changed)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self.file_table)
        splitter.addWidget(self.detail_panel)
        splitter.setSizes([SPLITTER_TOP_HEIGHT, SPLITTER_BOTTOM_HEIGHT])
        root_layout.addWidget(splitter)
        self.setCentralWidget(central)

    def _restore_table_layouts(self) -> None:
        """Restore saved file and detail column layouts when column counts match."""
        settings = SettingsManager().load()
        headers = (
            (
                self.file_table.horizontalHeader(),
                settings.file_table_layout,
                settings.file_table_column_count,
            ),
            (
                self.detail_panel.fields_table.horizontalHeader(),
                settings.detail_table_layout,
                settings.detail_table_column_count,
            ),
        )
        for header, encoded_state, saved_column_count in headers:
            if saved_column_count == header.count() and encoded_state:
                self._restore_header_state(header, encoded_state)
            header.sectionResized.connect(self._save_table_layouts)

    @staticmethod
    def _restore_header_state(header: QHeaderView, encoded_state: str) -> None:
        """Restore one header from a validated base64-encoded Qt state."""
        if not isinstance(encoded_state, str):
            return
        try:
            state = base64.b64decode(encoded_state, validate=True)
        except (binascii.Error, ValueError):
            return
        header.restoreState(QByteArray(state))

    def _save_table_layouts(self, *_args: int) -> None:
        """Persist file and detail header states after a user resizes a column."""
        settings_manager = SettingsManager()
        settings = settings_manager.load()
        file_header = self.file_table.horizontalHeader()
        detail_header = self.detail_panel.fields_table.horizontalHeader()
        settings.file_table_layout = base64.b64encode(bytes(file_header.saveState())).decode(
            "ascii"
        )
        settings.file_table_column_count = file_header.count()
        settings.detail_table_layout = base64.b64encode(bytes(detail_header.saveState())).decode(
            "ascii"
        )
        settings.detail_table_column_count = detail_header.count()
        settings_manager.save(settings)

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
            format_duration(asset.duration) if asset.duration is not None else "-",
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

    def _on_theme_changed(self, text: str) -> None:
        """Update and persist application UI theme."""
        settings_mgr = SettingsManager()
        settings = settings_mgr.load()
        settings.theme = text
        settings_mgr.save(settings)
        app = QApplication.instance()
        if app is not None:
            apply_application_theme(app, text)

    def _prompt_import_folder(self) -> None:
        """Open a folder picker and import the chosen folder in the background."""
        chosen = QFileDialog.getExistingDirectory(self, "Select Music Folder to Import")
        if chosen:
            self.start_folder_import(Path(chosen))

    def start_folder_import(self, folder: Path) -> bool:
        """Add a folder import to the batch queue. It runs on a worker thread. Return True."""
        self.batch_queue.enqueue_import([folder])
        return True

    def queue_batch(self, kind: BatchKind, label: str, run: BatchRun) -> QueuedBatch:
        """Add a library-changing batch to the queue. run executes on a worker thread."""
        return self.batch_queue.enqueue_job(kind, label, run)

    def _on_batch_finished(self, _batch: QueuedBatch) -> None:
        """Show the changes of a finished batch in the table and the detail panel."""
        self.refresh_file_table()
        if self.detail_panel.current_file_id:
            self.detail_panel.load_file_asset(self.detail_panel.current_file_id)

    def _on_queue_idle(self) -> None:
        """Close the window once the queue empties after Cancel and Exit."""
        if self._close_after_queue:
            self.close()

    def _update_action_locks(self) -> None:
        """Disable the library changes that run outside the queue while a batch is active.

        Transcode, journal restore, Bulk Approve, and field edits stay off until the
        queue is idle. Browsing, playback, filtering, and playlist export stay on.
        """
        idle = not self.batch_queue.is_busy()
        for widget in (
            self.bulk_btn,
            self.transcode_btn,
            self.journal_btn,
            self.detail_panel.fields_table,
        ):
            widget.setEnabled(idle)

    def show_batch_result(self, batch: QueuedBatch) -> None:
        """Open the result of a finished batch. Nothing opens it automatically."""
        if isinstance(batch.result, ImportBatchReport):
            ImportReportDialog(batch.result, self).open()
            return
        if batch.kind is BatchKind.IMPORT and batch.state is BatchState.FAILED:
            self._show_import_failure()
            return
        box = QMessageBox(
            QMessageBox.Icon.Information,
            "Batch Result",
            format_batch_result(batch),
            QMessageBox.StandardButton.Ok,
            self,
        )
        # The result shows file paths, so it stays plain text.
        box.setTextFormat(Qt.TextFormat.PlainText)
        self.batch_result_box = box
        box.open()

    def _show_import_failure(self) -> None:
        """Warn that the import rolled back. The message holds no exception text."""
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            "Import Failed",
            IMPORT_FAILURE_TEXT,
            QMessageBox.StandardButton.Ok,
            self,
        )
        box.setTextFormat(Qt.TextFormat.PlainText)
        self.import_failure_box = box
        box.open()

    def ask_close_during_import(self) -> str:
        """Ask whether to keep working or to cancel the batch queue and exit.

        Return CLOSE_CANCEL, or CLOSE_STAY when the person keeps working or dismisses
        the prompt. The prompt covers every batch kind. The method keeps its earlier
        name for existing callers.
        """
        box = QMessageBox(self)
        box.setWindowTitle("Batch In Progress")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(CLOSE_PROMPT_TEXT)
        box.addButton("Keep Working", QMessageBox.ButtonRole.RejectRole)
        cancel_button = box.addButton("Cancel and Exit", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        if box.clickedButton() is cancel_button:
            return CLOSE_CANCEL
        return CLOSE_STAY

    def closeEvent(self, event: QCloseEvent) -> None:
        """Close at once, or ask first while a batch runs or waits.

        Keep Working leaves the window open and the queue running. Cancel and Exit
        cancels the queue, and the window closes once the running batch stops. Later
        close requests during that wait ask nothing.
        """
        if not self.batch_queue.is_busy():
            super().closeEvent(event)
            return
        event.ignore()
        if self._close_after_queue:
            return
        if self.ask_close_during_import() != CLOSE_CANCEL:
            return
        self._close_after_queue = True
        self.batch_queue.cancel_all()

    def _trigger_analyze_all(self) -> None:
        """Queue identification and analysis of the visible files.

        Without an analyze_runner, the on_analyze_batch callback runs on this thread.
        """
        if not self.visible_file_ids:
            return
        file_ids = list(self.visible_file_ids)
        runner = self.analyze_runner
        if runner is not None:
            self.queue_batch(
                BatchKind.ANALYZE,
                f"Identify & Analyze {len(file_ids)} file(s)",
                lambda should_cancel, progress: runner(file_ids, should_cancel, progress),
            )
            return
        if self.on_analyze_batch:
            self.on_analyze_batch(file_ids)
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

    def _trigger_export_playlist(self) -> None:
        """Ask for a .m3u8 destination and invoke the playlist export callback."""
        if not self.on_export_playlist or not self.visible_file_ids:
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Export Playlist", "crate.m3u8", "M3U8 Playlist (*.m3u8)"
        )
        if not chosen:
            return
        destination = Path(chosen)
        # Append rather than replace, so a name like "set.v2" keeps its full stem.
        if destination.suffix.lower() != PLAYLIST_SUFFIX:
            destination = destination.with_name(destination.name + PLAYLIST_SUFFIX)
        self.on_export_playlist(list(self.visible_file_ids), destination)

    def _trigger_open_journal(self) -> None:
        """Open change journal and restore dialog if callback is registered."""
        if self.on_open_journal_dialog:
            self.on_open_journal_dialog()

    def _trigger_open_settings(self) -> None:
        """Open provider and setup settings dialog if callback is registered."""
        if self.on_open_settings_dialog:
            self.on_open_settings_dialog()
