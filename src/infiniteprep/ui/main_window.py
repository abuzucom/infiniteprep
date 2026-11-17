"""Main application window for InfinitePrep (Section 9)."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QByteArray, Qt, QTimer
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

from infiniteprep.config.settings import (
    SUPPORTED_UNSORTED_PLACEMENTS,
    UNSORTED_PLACEMENT_FIRST,
    SettingsManager,
)
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    FieldName,
    KeyDisplayNotation,
    ReviewStatus,
    format_canonical_key,
    parse_key_string,
)
from infiniteprep.domain.metadata_values import resolve_effective_value, top_proposal_values
from infiniteprep.domain.models import ApprovedField, FileAsset
from infiniteprep.services.import_service import ImportBatchReport, ImportService
from infiniteprep.services.job_orchestrator import BatchKind, BatchState, QueuedBatch
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.accessibility import set_combo_box_name
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
from infiniteprep.ui.table_sort import (
    FILENAME_COLUMN,
    SORT_COLUMN_COUNT,
    LibraryRow,
    parse_bpm_value,
    sort_rows,
)
from infiniteprep.ui.theme import (
    DEFAULT_DARK_THEME,
    NATIVE_THEME,
    get_theme_choices,
    switch_application_theme,
)
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
if len(TABLE_COLUMNS) != SORT_COLUMN_COUNT:
    raise RuntimeError("The library table columns and the sort kinds must stay aligned.")
ALL_FILES_FILTER: Final[str] = "All Files"
HAS_WARNINGS_FILTER: Final[str] = "Has Warnings (XDJ-RX3 / Rip)"
# Words that stay lower case inside a status label, as in "Ready to Write".
STATUS_LABEL_MINOR_WORDS: Final[frozenset[str]] = frozenset({"to", "for"})


def format_status_label(status: ReviewStatus) -> str:
    """Return the filter label of one review status, such as "Ready for Review"."""
    words = status.value.split("_")
    return " ".join(
        word if index and word in STATUS_LABEL_MINOR_WORDS else word.capitalize()
        for index, word in enumerate(words)
    )


# One filter entry per review status. The pipeline sets statuses such as no_match and
# ready_for_review, so a fixed short list left those files reachable only through All Files.
STATUS_FILTER_VALUES: Final[dict[str, str]] = {
    format_status_label(status): status.value for status in ReviewStatus
}
# Milliseconds after the last column resize before the layout saves.
LAYOUT_SAVE_DELAY_MS: Final[int] = 300
SEARCH_INPUT_STRETCH: Final[int] = 3
STATUS_FILTER_STRETCH: Final[int] = 1
SORT_ASCENDING_TEXT: Final[str] = "Ascending"
SORT_DESCENDING_TEXT: Final[str] = "Descending"
# Screen readers announce these names. Placeholder text vanishes once a field holds text.
SEARCH_INPUT_NAME: Final[str] = "Filter library"
STATUS_FILTER_NAME: Final[str] = "Filter by review status"
SORT_COLUMN_NAME: Final[str] = "Sort library by"
SORT_DIRECTION_NAME: Final[str] = "Sort direction"
KEY_NOTATION_NAME: Final[str] = "Key notation"
THEME_PICKER_NAME: Final[str] = "Theme"
FILE_TABLE_NAME: Final[str] = "Library"
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


@dataclass(frozen=True)
class LibrarySnapshot:
    """The per-file values one table refresh reads, loaded in three queries.

    A refresh read approved fields, proposals, and analysis results per file, twice for
    the analysis. A 5,000-file library ran about 20,000 queries on the GUI thread.
    """

    approved: dict[str, dict[str, ApprovedField]]
    top_proposals: dict[str, dict[str, str]]
    analysis_warnings: dict[str, list[str]]


def load_library_snapshot(repo: Repository) -> LibrarySnapshot:
    """Read the approved fields, top proposals, and analysis warnings of every file."""
    proposals = repo.field_proposals_by_file()
    analyses = repo.analysis_results_by_file()
    return LibrarySnapshot(
        approved=repo.approved_fields_by_file(),
        top_proposals={
            file_id: top_proposal_values(file_proposals)
            for file_id, file_proposals in proposals.items()
        },
        analysis_warnings={
            file_id: [warning for result in results for warning in result.warnings]
            for file_id, results in analyses.items()
        },
    )


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
        self._init_sort_state()

        self.setWindowTitle("InfinitePrep - DJ Music Metadata & Audio Preparation")
        self.resize(MAIN_WINDOW_WIDTH, MAIN_WINDOW_HEIGHT)

        self.search_input = QLineEdit(self)
        self.status_filter_combo = QComboBox(self)
        self.key_notation_combo = QComboBox(self)
        self.theme_combo = QComboBox(self)
        self._applied_theme = NATIVE_THEME
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
        self.batch_queue = BatchQueueController(
            self.import_controller, self.repo.db.close, self, prompt_parent=self
        )
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
        set_combo_box_name(self.key_notation_combo, KEY_NOTATION_NAME)
        toolbar.addWidget(self.key_notation_combo)

        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Theme: ", self))
        for theme_name in get_theme_choices():
            self.theme_combo.addItem(theme_name)
        current_theme = getattr(SettingsManager().load(), "theme", DEFAULT_DARK_THEME)
        self._applied_theme = self._selectable_theme(current_theme)
        self.theme_combo.setCurrentText(self._applied_theme)
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        set_combo_box_name(self.theme_combo, THEME_PICKER_NAME)
        toolbar.addWidget(self.theme_combo)

    def _selectable_theme(self, theme: str) -> str:
        """Return the theme to show, falling back to a theme the picker offers."""
        choices = get_theme_choices()
        if theme in choices:
            return theme
        return choices[0] if choices else NATIVE_THEME

    def _setup_central_widget(self) -> None:
        """Build filter bar, file table, and vertical/horizontal splitter with DetailPanel."""
        central = QWidget(self)
        root_layout = QVBoxLayout(central)

        filter_bar = QHBoxLayout()
        self.search_input.setPlaceholderText(
            "Filter by filename, artist, title, label, or warning..."
        )
        self.search_input.textChanged.connect(self.refresh_file_table)
        self.search_input.setAccessibleName(SEARCH_INPUT_NAME)
        filter_bar.addWidget(self.search_input, stretch=SEARCH_INPUT_STRETCH)

        self.status_filter_combo.addItems(
            [ALL_FILES_FILTER, HAS_WARNINGS_FILTER, *STATUS_FILTER_VALUES]
        )
        self.status_filter_combo.currentTextChanged.connect(self.refresh_file_table)
        set_combo_box_name(self.status_filter_combo, STATUS_FILTER_NAME)
        filter_bar.addWidget(self.status_filter_combo, stretch=STATUS_FILTER_STRETCH)
        self._add_sort_controls(filter_bar)
        root_layout.addLayout(filter_bar)

        self.file_table.setHorizontalHeaderLabels(list(TABLE_COLUMNS))
        self.file_table.setAccessibleName(FILE_TABLE_NAME)
        # The cells show computed values. An edit in a cell changed nothing and was lost.
        self.file_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        file_header = self.file_table.horizontalHeader()
        file_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        file_header.setStretchLastSection(True)
        file_header.setSectionsClickable(True)
        self.file_table.setSortingEnabled(False)
        file_header.setSortIndicatorShown(True)
        file_header.setSortIndicator(self._sort_column, Qt.SortOrder.AscendingOrder)
        file_header.sectionClicked.connect(self._on_table_header_clicked)
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Tab leaves the table in one press. The arrow keys move between cells.
        self.file_table.setTabKeyNavigation(False)
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
        # A column drag emits sectionResized for each pixel. One save runs after the drag
        # settles, so the settings file is written once per resize, not once per pixel.
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(LAYOUT_SAVE_DELAY_MS)
        self._layout_save_timer.timeout.connect(self._save_table_layouts)
        for header, encoded_state, saved_column_count in headers:
            if saved_column_count == header.count() and encoded_state:
                self._restore_header_state(header, encoded_state)
            header.sectionResized.connect(self._schedule_layout_save)

    def _schedule_layout_save(self, *_args: int) -> None:
        """Restart the layout save timer after a column resize."""
        self._layout_save_timer.start()

    def flush_layout_save(self) -> None:
        """Save a pending column layout now. A close must not lose the last resize."""
        if self._layout_save_timer.isActive():
            self._layout_save_timer.stop()
            self._save_table_layouts()

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

    def _save_table_layouts(self) -> None:
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
        snapshot = load_library_snapshot(self.repo)
        filtered = [f for f in all_files if self._matches_status_filter(f, snapshot)]
        rows = [self._build_library_row(asset, snapshot) for asset in filtered]
        query = self.search_input.text().strip().casefold()
        if query:
            # The search reads the cells the table shows, so a match is always visible.
            rows = [row for row in rows if query in " ".join(row.cells).casefold()]
        ordered = sort_rows(
            rows,
            column=self._sort_column,
            descending=self._sort_descending,
            placement=self._unsorted_placement,
            notation=self.current_key_notation,
        )
        self.visible_file_ids = [row.file_id for row in ordered]

        self.file_table.blockSignals(True)
        self.file_table.setRowCount(len(ordered))
        for row_idx, row in enumerate(ordered):
            self._populate_table_row(row_idx, row)
        self.file_table.blockSignals(False)
        self._restore_selected_file()

    def _init_sort_state(self) -> None:
        """Set the starting sort column, direction, selection, and placement."""
        self._sort_column: int = FILENAME_COLUMN
        self._sort_descending: bool = False
        self._selected_file_id: str | None = None
        self._unsorted_placement: str = self._read_unsorted_placement()

    def _read_unsorted_placement(self) -> str:
        """Return the stored placement of the rows without a value for the sorted column."""
        settings = SettingsManager().load()
        placement = getattr(settings, "unsorted_rows_placement", UNSORTED_PLACEMENT_FIRST)
        if placement in SUPPORTED_UNSORTED_PLACEMENTS:
            return placement
        return UNSORTED_PLACEMENT_FIRST

    def reload_settings(self) -> None:
        """Read the stored preferences again and repaint the table.

        The Settings dialog applies a theme itself. The toolbar picker follows the stored
        theme, so a later toolbar choice compares against the theme in effect.
        """
        self._unsorted_placement = self._read_unsorted_placement()
        stored_theme = getattr(SettingsManager().load(), "theme", DEFAULT_DARK_THEME)
        self._applied_theme = self._selectable_theme(stored_theme)
        self._set_picker_theme(self._applied_theme)
        self.refresh_file_table()

    def _add_sort_controls(self, filter_bar: QHBoxLayout) -> None:
        """Add a sort column picker and a direction button for keyboard users.

        The table header takes no keyboard focus, so a header click is the only other
        way to sort. The controls and the header stay in step.
        """
        sort_label = QLabel("Sort by:", self)
        self.sort_column_combo = QComboBox(self)
        self.sort_column_combo.addItems(list(TABLE_COLUMNS))
        self.sort_column_combo.setCurrentIndex(self._sort_column)
        set_combo_box_name(self.sort_column_combo, SORT_COLUMN_NAME)
        sort_label.setBuddy(self.sort_column_combo)
        self.sort_direction_btn = QPushButton(SORT_ASCENDING_TEXT, self)
        self.sort_direction_btn.setAccessibleName(SORT_DIRECTION_NAME)
        self.sort_column_combo.currentIndexChanged.connect(self._on_sort_column_chosen)
        self.sort_direction_btn.clicked.connect(self._on_sort_direction_toggled)
        filter_bar.addWidget(sort_label)
        filter_bar.addWidget(self.sort_column_combo)
        filter_bar.addWidget(self.sort_direction_btn)

    def _on_table_header_clicked(self, column: int) -> None:
        """Sort by the clicked column and reverse the direction on a second click."""
        if column == self._sort_column:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_column = column
            self._sort_descending = False
        self._apply_sort()

    def _on_sort_column_chosen(self, column: int) -> None:
        """Sort ascending by the column chosen in the sort picker."""
        if column < 0 or column == self._sort_column:
            return
        self._sort_column = column
        self._sort_descending = False
        self._apply_sort()

    def _on_sort_direction_toggled(self) -> None:
        """Reverse the sort direction of the current column."""
        self._sort_descending = not self._sort_descending
        self._apply_sort()

    def _apply_sort(self) -> None:
        """Show the sort state on the header and the sort controls, then repaint."""
        order = (
            Qt.SortOrder.DescendingOrder if self._sort_descending else Qt.SortOrder.AscendingOrder
        )
        self.file_table.horizontalHeader().setSortIndicator(self._sort_column, order)
        self.sort_column_combo.blockSignals(True)
        self.sort_column_combo.setCurrentIndex(self._sort_column)
        self.sort_column_combo.blockSignals(False)
        self.sort_direction_btn.setText(
            SORT_DESCENDING_TEXT if self._sort_descending else SORT_ASCENDING_TEXT
        )
        self.refresh_file_table()

    def _restore_selected_file(self) -> None:
        """Keep the selected file selected after the table repaints."""
        if not self._selected_file_id:
            return
        if self._selected_file_id not in self.visible_file_ids:
            return
        row_idx = self.visible_file_ids.index(self._selected_file_id)
        self.file_table.selectRow(row_idx)

    @staticmethod
    def _asset_warnings(asset: FileAsset, snapshot: LibrarySnapshot) -> list[str]:
        """Collect technical and analysis warnings for one library asset."""
        warnings: list[str] = list(asset.original_tags.get("technical", {}).get("warnings", []))
        warnings.extend(snapshot.analysis_warnings.get(asset.id, []))
        return warnings

    def _matches_status_filter(self, asset: FileAsset, snapshot: LibrarySnapshot) -> bool:
        """Return True if asset matches the status filter dropdown."""
        status_choice = self.status_filter_combo.currentText()
        if status_choice == HAS_WARNINGS_FILTER:
            return bool(self._asset_warnings(asset, snapshot))
        status_value = STATUS_FILTER_VALUES.get(status_choice)
        return status_value is None or str(asset.review_status) == status_value

    def _populate_table_row(self, row_idx: int, row: LibraryRow) -> None:
        """Render one library row and store its file ID on the first cell."""
        for col_idx, text in enumerate(row.cells):
            item = QTableWidgetItem(text)
            if col_idx == 0:
                item.setData(Qt.ItemDataRole.UserRole, row.file_id)
            self.file_table.setItem(row_idx, col_idx, item)

    def _build_library_row(self, asset: FileAsset, snapshot: LibrarySnapshot) -> LibraryRow:
        """Build the table row for one asset with the values the sort needs."""
        std = asset.original_tags.get("standard_tags", {})
        approved = snapshot.approved.get(asset.id, {})
        top_by_field = snapshot.top_proposals.get(asset.id, {})
        raw_key = self._effective_val(std, approved, top_by_field, FieldName.KEY.value)
        raw_bpm = self._effective_val(std, approved, top_by_field, FieldName.BPM.value)
        warnings = self._asset_warnings(asset, snapshot)
        cells = self._build_table_row_cells(asset, std, approved, top_by_field, warnings)
        return LibraryRow(
            file_id=asset.id,
            cells=tuple(cells),
            path=asset.current_path,
            duration_seconds=asset.duration,
            bpm=parse_bpm_value(raw_bpm),
            key=parse_key_string(raw_key),
            review_status=str(asset.review_status),
        )

    def _build_table_row_cells(
        self,
        asset: FileAsset,
        std: dict[str, str],
        approved: dict,
        top_by_field: dict[str, str],
        warnings: list[str],
    ) -> list[str]:
        """Compute string cells for a single FileAsset row in the batch review table."""
        lbl_str = self._effective_val(std, approved, top_by_field, FieldName.LABEL.value)
        cat_str = self._effective_val(std, approved, top_by_field, FieldName.CATALOG_NUMBER.value)
        raw_key = self._effective_val(std, approved, top_by_field, FieldName.KEY.value)

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
        return resolve_effective_value(std, approved, top_by_field, field_name)

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
        item = self.file_table.item(row_idx, 0)
        if item is None:
            return
        file_id = item.data(Qt.ItemDataRole.UserRole)
        if not file_id:
            return
        self._selected_file_id = str(file_id)
        self.detail_panel.load_file_asset(self._selected_file_id)

    def _on_key_notation_changed(self, text: str) -> None:
        """Update key notation across table and DetailPanel."""
        for notation in KeyDisplayNotation:
            if notation.value == text:
                self.current_key_notation = notation
                break
        self.detail_panel.set_key_notation(self.current_key_notation)
        self.refresh_file_table()

    def _on_theme_changed(self, text: str) -> None:
        """Apply the chosen theme and keep the picker on the theme in effect."""
        app = QApplication.instance()
        result = switch_application_theme(app, text, self._applied_theme)
        self._applied_theme = result.theme
        self._set_picker_theme(result.theme)
        settings_mgr = SettingsManager()
        settings = settings_mgr.load()
        settings.theme = result.theme
        settings_mgr.save(settings)

    def _set_picker_theme(self, theme: str) -> None:
        """Point the picker at the given theme without firing the change signal."""
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentText(theme)
        self.theme_combo.blockSignals(False)

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
            self.flush_layout_save()
            # A child widget gets no closeEvent of its own. Closing the panel cancels
            # its analysis so no late result writes after the window closes.
            self.detail_panel.close()
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
