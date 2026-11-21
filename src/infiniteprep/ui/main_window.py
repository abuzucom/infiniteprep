"""Main application window for InfinitePrep (Section 9)."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QByteArray, QMimeData, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QDrag, QKeyEvent, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
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
from infiniteprep.ui.accessible_names import announce, name_combo_box
from infiniteprep.ui.action_handlers import show_plain_message
from infiniteprep.ui.audio_player import FILE_ID_MIME_TYPE
from infiniteprep.ui.batch_queue import (
    BatchQueueController,
    BatchRun,
    CancelCheck,
    ProgressReport,
    format_batch_result,
    format_batch_row,
)
from infiniteprep.ui.batch_queue_dock import BatchQueueDock
from infiniteprep.ui.column_sizing import WeightedColumnSizer
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
    fill_theme_picker,
    get_theme_choices,
    select_theme,
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
WARNINGS_COLUMN: Final[int] = TABLE_COLUMNS.index("Hardware / Rip Warnings")
# Filename, Artist, and Title take two parts of the spare width each. Mix / Version and
# Label / Cat# take one part each. Every other column fits its cells (owner, UI-1).
LIBRARY_COLUMN_WEIGHTS: Final[dict[int, int]] = {
    TABLE_COLUMNS.index("Filename"): 2,
    TABLE_COLUMNS.index("Artist (Cur -> Prop)"): 2,
    TABLE_COLUMNS.index("Title (Cur -> Prop)"): 2,
    TABLE_COLUMNS.index("Mix / Version"): 1,
    TABLE_COLUMNS.index("Label / Cat#"): 1,
}
# Below this share a weighted column stops shrinking and the table scrolls sideways.
LIBRARY_MIN_SHARE_PX: Final[int] = 80
# The Key Notation box shows these names. Its item data keeps the stored notation value.
KEY_NOTATION_LABELS: Final[dict[KeyDisplayNotation, str]] = {
    KeyDisplayNotation.STANDARD: "Standard",
    KeyDisplayNotation.CAMELOT: "Camelot",
    KeyDisplayNotation.OPEN_KEY: "Open Key",
}
WARNING_LINE_SEPARATOR: Final[str] = "\n"
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


def format_warning_summary(warnings: list[str]) -> str:
    """Return the warnings cell text: a count such as "2 warnings", or nothing for none."""
    count = len(warnings)
    if count == 0:
        return ""
    noun = "warning" if count == 1 else "warnings"
    return f"{count} {noun}"


def format_status_cell(value: str) -> str:
    """Return the label a status cell shows, such as "Ready to Write".

    An unknown stored value shows as stored, so no row loses its status.
    """
    try:
        status = ReviewStatus(value)
    except ValueError:
        return value
    return format_status_label(status)


# One filter entry per review status. The pipeline sets statuses such as no_match and
# ready_for_review, so a fixed short list left those files reachable only through All Files.
STATUS_FILTER_VALUES: Final[dict[str, str]] = {
    format_status_label(status): status.value for status in ReviewStatus
}
# Milliseconds after the last column resize before the layout saves.
LAYOUT_SAVE_DELAY_MS: Final[int] = 300
SEARCH_INPUT_STRETCH: Final[int] = 3
STATUS_FILTER_STRETCH: Final[int] = 1
NO_VISIBLE_FILES_TEXT: Final[str] = (
    "No files are visible. Import a folder, or clear the filter and the status choice."
)
ANALYZE_TITLE: Final[str] = "Identify & Analyze All"
BULK_APPROVE_TITLE: Final[str] = "Bulk Approve Non-Conflicting"
BULK_APPROVE_RESULT_TEMPLATE: Final[str] = "Approved {count} field(s) where every proposal agreed."
WRITE_TITLE: Final[str] = "Preview & Apply Writes"
TRANSCODE_TITLE: Final[str] = "Transcode to XDJ"
EXPORT_TITLE: Final[str] = "Export Playlist (.m3u8)"
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
# Toolbar labels in Qt mnemonic form. A single ampersand marks the Alt key and stays
# hidden. A doubled ampersand shows one ampersand. Screen readers read the shown text.
IMPORT_BUTTON_LABEL: Final[str] = "&Import Folder"
ANALYZE_BUTTON_LABEL: Final[str] = "Identify && &Analyze All"
BULK_APPROVE_BUTTON_LABEL: Final[str] = "&Bulk Approve Non-Conflicting"
WRITE_BUTTON_LABEL: Final[str] = "&Preview && Apply Writes"
TRANSCODE_BUTTON_LABEL: Final[str] = "&Transcode to XDJ"
EXPORT_BUTTON_LABEL: Final[str] = "&Export Playlist (.m3u8)"
JOURNAL_BUTTON_LABEL: Final[str] = "Change &Journal && Restore"
SETTINGS_BUTTON_LABEL: Final[str] = "&Settings && Providers"
KEY_NOTATION_LABEL: Final[str] = " &Key Notation: "
THEME_LABEL: Final[str] = " T&heme: "
SETTINGS_SHORTCUT: Final[str] = "Ctrl+,"
# Space stays with the focused button or field, so Play/Pause takes Ctrl+P.
PLAY_PAUSE_SHORTCUT: Final[str] = "Ctrl+P"
MEDIA_PLAY_PAUSE_SHORTCUT: Final[str] = "Media Play/Pause"
PLAY_BUTTON_TOOLTIP_TEMPLATE: Final[str] = "Play or pause the preview ({shortcut})"
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
# The first column carries the file ID of the row.
FILE_ID_COLUMN: Final[int] = 0
PLAY_MENU_ITEM_LABEL: Final[str] = "Play"


class LibraryTableWidget(QTableWidget):
    """Library table that carries a file ID in a drag and asks for a play on Ctrl+Enter."""

    play_selection_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the table as a drag source for library rows."""
        super().__init__(parent)
        self._drag_press_pos: QPoint | None = None
        self.setAcceptDrops(False)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Record the press position so a drag carries the row under the pointer."""
        self._drag_press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Ask for the selected track to play on Ctrl+Enter."""
        if (
            event.key() == Qt.Key.Key_Return
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.play_selection_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions: Qt.DropActions) -> None:
        """Start a drag that carries the file ID of the dragged row."""
        mime_data = self.drag_mime_data()
        if mime_data is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.CopyAction)

    def drag_mime_data(self) -> QMimeData | None:
        """Return the payload of the dragged row, or None when the row holds no file ID."""
        index = self.currentIndex()
        if self._drag_press_pos is not None:
            pressed_index = self.indexAt(self._drag_press_pos)
            if pressed_index.isValid():
                index = pressed_index
        if not index.isValid():
            return None
        item = self.item(index.row(), FILE_ID_COLUMN)
        if item is None:
            return None
        file_id = item.data(Qt.ItemDataRole.UserRole)
        if not file_id:
            return None
        mime_data = QMimeData()
        mime_data.setData(FILE_ID_MIME_TYPE, str(file_id).encode("utf-8"))
        return mime_data


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
        self.file_table = LibraryTableWidget(self)
        self.file_table.setColumnCount(len(TABLE_COLUMNS))
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

        self._add_toolbar_button(toolbar, IMPORT_BUTTON_LABEL, self._prompt_import_folder)
        self._add_toolbar_button(toolbar, ANALYZE_BUTTON_LABEL, self._trigger_analyze_all)
        self.bulk_btn = self._add_toolbar_button(
            toolbar, BULK_APPROVE_BUTTON_LABEL, self._trigger_bulk_approve
        )
        self._add_toolbar_button(toolbar, WRITE_BUTTON_LABEL, self._trigger_open_write_dialog)
        self.transcode_btn = self._add_toolbar_button(
            toolbar, TRANSCODE_BUTTON_LABEL, self._trigger_transcode_batch
        )
        self.export_playlist_btn = self._add_toolbar_button(
            toolbar, EXPORT_BUTTON_LABEL, self._trigger_export_playlist
        )
        self.journal_btn = self._add_toolbar_button(
            toolbar, JOURNAL_BUTTON_LABEL, self._trigger_open_journal
        )
        self._add_toolbar_button(toolbar, SETTINGS_BUTTON_LABEL, self._trigger_open_settings)

        toolbar.addSeparator()
        self._add_picker_label(toolbar, KEY_NOTATION_LABEL, self.key_notation_combo)
        for notation in KeyDisplayNotation:
            self.key_notation_combo.addItem(KEY_NOTATION_LABELS[notation], notation.value)
        self.key_notation_combo.setCurrentIndex(
            self.key_notation_combo.findData(self.current_key_notation.value)
        )
        self.key_notation_combo.currentIndexChanged.connect(self._on_key_notation_picked)
        name_combo_box(self.key_notation_combo, KEY_NOTATION_NAME)
        toolbar.addWidget(self.key_notation_combo)

        toolbar.addSeparator()
        self._add_picker_label(toolbar, THEME_LABEL, self.theme_combo)
        fill_theme_picker(self.theme_combo, get_theme_choices())
        current_theme = getattr(SettingsManager().load(), "theme", DEFAULT_DARK_THEME)
        self._applied_theme = self._selectable_theme(current_theme)
        select_theme(self.theme_combo, self._applied_theme)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_picked)
        name_combo_box(self.theme_combo, THEME_PICKER_NAME)
        toolbar.addWidget(self.theme_combo)
        # The shortcuts sit beside the toolbar actions they mirror.
        self._setup_shortcuts()

    def _add_toolbar_button(
        self, toolbar: QToolBar, label: str, handler: Callable[[], None]
    ) -> QPushButton:
        """Add one toolbar push button with a mnemonic label and return it."""
        button = QPushButton(label, self)
        button.clicked.connect(handler)
        toolbar.addWidget(button)
        return button

    def _add_picker_label(self, toolbar: QToolBar, label: str, picker: QComboBox) -> None:
        """Add a mnemonic label whose Alt key moves focus to the picker after it."""
        picker_label = QLabel(label, self)
        picker_label.setBuddy(picker)
        toolbar.addWidget(picker_label)

    def _setup_shortcuts(self) -> None:
        """Bind window-wide keys for Import, Settings, and preview Play/Pause."""
        context = Qt.ShortcutContext.WindowShortcut
        self.import_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Open), self)
        self.import_shortcut.setContext(context)
        self.import_shortcut.activated.connect(self._prompt_import_folder)
        self.settings_shortcut = QShortcut(QKeySequence(SETTINGS_SHORTCUT), self)
        self.settings_shortcut.setContext(context)
        self.settings_shortcut.activated.connect(self._trigger_open_settings)
        # Both keys press the player button, so a new selection loads before it plays.
        play_button = self.detail_panel.audio_player.play_btn
        self.play_pause_shortcut = QShortcut(QKeySequence(PLAY_PAUSE_SHORTCUT), self)
        self.media_play_pause_shortcut = QShortcut(QKeySequence(MEDIA_PLAY_PAUSE_SHORTCUT), self)
        for shortcut in (self.play_pause_shortcut, self.media_play_pause_shortcut):
            shortcut.setContext(context)
            shortcut.activated.connect(play_button.click)
        shown_key = self.play_pause_shortcut.key().toString(QKeySequence.SequenceFormat.NativeText)
        play_button.setToolTip(PLAY_BUTTON_TOOLTIP_TEMPLATE.format(shortcut=shown_key))

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
        self.search_input.setPlaceholderText("Filter by filename, artist, title, or label...")
        self.search_input.textChanged.connect(self.refresh_file_table)
        self.search_input.setAccessibleName(SEARCH_INPUT_NAME)
        filter_bar.addWidget(self.search_input, stretch=SEARCH_INPUT_STRETCH)

        self.status_filter_combo.addItems(
            [ALL_FILES_FILTER, HAS_WARNINGS_FILTER, *STATUS_FILTER_VALUES]
        )
        self.status_filter_combo.currentTextChanged.connect(self.refresh_file_table)
        name_combo_box(self.status_filter_combo, STATUS_FILTER_NAME)
        filter_bar.addWidget(self.status_filter_combo, stretch=STATUS_FILTER_STRETCH)
        self._add_sort_controls(filter_bar)
        root_layout.addLayout(filter_bar)

        self.file_table.setHorizontalHeaderLabels(list(TABLE_COLUMNS))
        # A fitted column can be narrower than its header, which then elides.
        for column_index, header_text in enumerate(TABLE_COLUMNS):
            self.file_table.horizontalHeaderItem(column_index).setToolTip(header_text)
        self.file_table.setAccessibleName(FILE_TABLE_NAME)
        # The cells show computed values. An edit in a cell changed nothing and was lost.
        self.file_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.file_sizer = WeightedColumnSizer(
            self.file_table, LIBRARY_COLUMN_WEIGHTS, LIBRARY_MIN_SHARE_PX
        )
        file_header = self.file_table.horizontalHeader()
        file_header.setSectionsClickable(True)
        self.file_table.setSortingEnabled(False)
        file_header.setSortIndicatorShown(True)
        file_header.setSortIndicator(self._sort_column, Qt.SortOrder.AscendingOrder)
        file_header.sectionClicked.connect(self._on_table_header_clicked)
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Tab leaves the table in one press. The arrow keys move between cells.
        self.file_table.setTabKeyNavigation(False)
        self.file_table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.file_table.cellDoubleClicked.connect(self._on_table_cell_double_clicked)
        self.file_table.play_selection_requested.connect(self._play_selected_file)
        self.file_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_table.customContextMenuRequested.connect(self._on_library_context_menu)

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
                self.file_sizer,
            ),
            (
                self.detail_panel.fields_table.horizontalHeader(),
                settings.detail_table_layout,
                settings.detail_table_column_count,
                self.detail_panel.fields_sizer,
            ),
        )
        # A column drag emits sectionResized for each pixel. One save runs after the drag
        # settles, so the settings file is written once per resize, not once per pixel.
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(LAYOUT_SAVE_DELAY_MS)
        self._layout_save_timer.timeout.connect(self._save_table_layouts)
        for header, encoded_state, saved_column_count, sizer in headers:
            saved_layout_fits = saved_column_count == header.count() and bool(encoded_state)
            # A saved layout keeps every width as saved (owner, UI-1).
            if saved_layout_fits and self._restore_header_state(header, encoded_state):
                sizer.keep_restored_widths()
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
    def _restore_header_state(header: QHeaderView, encoded_state: str) -> bool:
        """Restore one header from a validated base64-encoded Qt state.

        Return True when Qt accepted the state.
        """
        if not isinstance(encoded_state, str):
            return False
        try:
            state = base64.b64decode(encoded_state, validate=True)
        except (binascii.Error, ValueError):
            return False
        return bool(header.restoreState(QByteArray(state)))

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
        filtered = [
            file_asset
            for file_asset in all_files
            if self._matches_status_filter(file_asset, snapshot)
        ]
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

        # The repopulation empties the table selection for a moment. The panel
        # keeps the file while the refresh runs, and the selection is restored
        # before the refresh ends.
        self._refreshing_table = True
        try:
            self.file_table.blockSignals(True)
            self.file_table.setRowCount(len(ordered))
            for row_index, row in enumerate(ordered):
                self._populate_table_row(row_index, row)
            self.file_table.blockSignals(False)
            self.file_sizer.refit()
            self._restore_selected_file()
        finally:
            self._refreshing_table = False
        self._sync_detail_panel_with_selection()

    def _init_sort_state(self) -> None:
        """Set the starting sort column, direction, selection, and placement."""
        self._sort_column: int = FILENAME_COLUMN
        self._sort_descending: bool = False
        self._selected_file_id: str | None = None
        self._refreshing_table = False
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
        name_combo_box(self.sort_column_combo, SORT_COLUMN_NAME)
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
        row_index = self.visible_file_ids.index(self._selected_file_id)
        self.file_table.selectRow(row_index)

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

    def _populate_table_row(self, row_index: int, row: LibraryRow) -> None:
        """Render one library row and store its file ID on the first cell."""
        for column_index, text in enumerate(row.cells):
            item = QTableWidgetItem(text)
            if column_index == 0:
                item.setData(Qt.ItemDataRole.UserRole, row.file_id)
            if column_index == WARNINGS_COLUMN and row.warning_details:
                item.setToolTip(row.warning_details)
            self.file_table.setItem(row_index, column_index, item)

    def _build_library_row(self, asset: FileAsset, snapshot: LibrarySnapshot) -> LibraryRow:
        """Build the table row for one asset with the values the sort needs."""
        standard_tags = asset.original_tags.get("standard_tags", {})
        approved = snapshot.approved.get(asset.id, {})
        top_by_field = snapshot.top_proposals.get(asset.id, {})
        raw_key = self._effective_val(standard_tags, approved, top_by_field, FieldName.KEY.value)
        raw_bpm = self._effective_val(standard_tags, approved, top_by_field, FieldName.BPM.value)
        warnings = self._asset_warnings(asset, snapshot)
        cells = self._build_table_row_cells(asset, standard_tags, approved, top_by_field, warnings)
        return LibraryRow(
            file_id=asset.id,
            cells=tuple(cells),
            path=asset.current_path,
            duration_seconds=asset.duration,
            bpm=parse_bpm_value(raw_bpm),
            key=parse_key_string(raw_key),
            review_status=str(asset.review_status),
            warning_details=WARNING_LINE_SEPARATOR.join(warnings),
        )

    def _build_table_row_cells(
        self,
        asset: FileAsset,
        standard_tags: dict[str, str],
        approved: dict,
        top_by_field: dict[str, str],
        warnings: list[str],
    ) -> list[str]:
        """Compute string cells for a single FileAsset row in the batch review table."""
        label_text = self._effective_val(
            standard_tags, approved, top_by_field, FieldName.LABEL.value
        )
        catalog_number_text = self._effective_val(
            standard_tags, approved, top_by_field, FieldName.CATALOG_NUMBER.value
        )
        raw_key = self._effective_val(standard_tags, approved, top_by_field, FieldName.KEY.value)

        return [
            Path(asset.current_path).name,
            str(asset.format).upper(),
            format_duration(asset.duration) if asset.duration is not None else "-",
            self._summary_pair(standard_tags, approved, top_by_field, FieldName.ARTIST.value),
            self._summary_pair(standard_tags, approved, top_by_field, FieldName.TITLE.value),
            self._effective_val(standard_tags, approved, top_by_field, FieldName.MIX_NAME.value),
            f"{label_text} [{catalog_number_text}]" if catalog_number_text else label_text,
            self._effective_val(standard_tags, approved, top_by_field, FieldName.BPM.value),
            self._format_key_cell(raw_key),
            format_status_cell(str(asset.review_status)),
            format_warning_summary(warnings),
        ]

    def _summary_pair(
        self,
        standard_tags: dict[str, str],
        approved: dict,
        top_by_field: dict[str, str],
        field_name: str,
    ) -> str:
        """Format 'Current -> Approved/Proposed' summary string for table cells."""
        current_value = standard_tags.get(field_name, "-")
        target = (
            approved[field_name].approved_value
            if field_name in approved
            else top_by_field.get(field_name, "")
        )
        if target and target != current_value:
            return f"{current_value} -> {target}"
        return target or current_value

    def _effective_val(
        self,
        standard_tags: dict[str, str],
        approved: dict,
        top_by_field: dict[str, str],
        field_name: str,
    ) -> str:
        """Return approved value, top proposal, or existing standard tag."""
        return resolve_effective_value(standard_tags, approved, top_by_field, field_name)

    def _format_key_cell(self, raw_key: str) -> str:
        """Format musical key according to active KeyDisplayNotation."""
        parsed = parse_key_string(raw_key)
        if parsed is None:
            return raw_key
        return format_canonical_key(parsed[0], parsed[1], self.current_key_notation)

    def _on_table_selection_changed(self) -> None:
        """Show the selected file in DetailPanel when table selection changes.

        The selection never touches the player. The player keeps its track, its
        position, and its play state until an explicit load (D407). A cleared
        selection leaves no file behind in the panel.
        """
        selected_rows = self.file_table.selectionModel().selectedRows()
        if not selected_rows:
            if not self._refreshing_table:
                self._clear_selection()
            return
        row_index = selected_rows[0].row()
        item = self.file_table.item(row_index, FILE_ID_COLUMN)
        if item is None:
            return
        file_id = item.data(Qt.ItemDataRole.UserRole)
        if not file_id:
            return
        self._selected_file_id = str(file_id)
        self.detail_panel.select_file_asset(self._selected_file_id)

    def _clear_selection(self) -> None:
        """Drop the selection from the window and from the detail panel."""
        self._selected_file_id = None
        self.detail_panel.clear_selection()

    def _sync_detail_panel_with_selection(self) -> None:
        """Show the selected file in the detail panel after a refresh."""
        file_id = self._selected_row_file_id()
        if file_id is None:
            return
        self._selected_file_id = file_id
        self.detail_panel.select_file_asset(file_id)

    def _file_id_for_row(self, row: int) -> str | None:
        """Return the file ID of the table row, or None when the row holds none."""
        item = self.file_table.item(row, FILE_ID_COLUMN)
        if item is None:
            return None
        file_id = item.data(Qt.ItemDataRole.UserRole)
        return str(file_id) if file_id else None

    def _selected_row_file_id(self) -> str | None:
        """Return the file ID of the selected row, or None when no row is selected."""
        selected_rows = self.file_table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        return self._file_id_for_row(selected_rows[0].row())

    def _play_selected_file(self) -> None:
        """Load and play the file the library table has selected."""
        file_id = self._selected_row_file_id()
        if file_id is None:
            return
        self.detail_panel.load_and_play_file_asset(file_id)

    def _on_table_cell_double_clicked(self, row: int, column: int) -> None:
        """Load and play the double-clicked row."""
        file_id = self._file_id_for_row(row)
        if file_id is None:
            return
        self.detail_panel.load_and_play_file_asset(file_id)

    def _on_library_context_menu(self, position: QPoint) -> None:
        """Open the context menu of the row under the pointer.

        The right-click selects the row first, so the menu acts on the row the
        pointer rests on. The action takes its file ID from that row. A click
        outside every row opens no menu, so the action never targets another row.
        """
        index = self.file_table.indexAt(position)
        if not index.isValid():
            return
        file_id = self._file_id_for_row(index.row())
        if file_id is None:
            return
        self.file_table.selectRow(index.row())
        menu = QMenu(self.file_table)
        play_action = menu.addAction(PLAY_MENU_ITEM_LABEL)
        play_action.triggered.connect(lambda: self.detail_panel.load_and_play_file_asset(file_id))
        self._exec_context_menu(menu, position)

    def _exec_context_menu(self, menu: QMenu, position: QPoint) -> None:
        """Show the context menu at the pointer position."""
        menu.exec(self.file_table.viewport().mapToGlobal(position))

    def _on_key_notation_picked(self, index: int) -> None:
        """Apply the notation whose name the box shows. The item data holds its value."""
        value = self.key_notation_combo.itemData(index)
        if isinstance(value, str):
            self._on_key_notation_changed(value)

    def _on_key_notation_changed(self, text: str) -> None:
        """Update key notation across table and DetailPanel."""
        for notation in KeyDisplayNotation:
            if notation.value == text:
                self.current_key_notation = notation
                break
        self.detail_panel.set_key_notation(self.current_key_notation)
        self.refresh_file_table()

    def _on_theme_picked(self, index: int) -> None:
        """Apply the theme whose label the picker shows. The item data holds its identifier."""
        theme = self.theme_combo.itemData(index)
        if isinstance(theme, str):
            self._on_theme_changed(theme)

    def _on_theme_changed(self, text: str) -> None:
        """Apply the chosen theme and keep the picker on the theme in effect."""
        app = QApplication.instance()
        result = switch_application_theme(app, text, self._applied_theme)
        self._applied_theme = result.theme
        self._set_picker_theme(result.theme)
        settings_manager = SettingsManager()
        settings = settings_manager.load()
        settings.theme = result.theme
        settings_manager.save(settings)

    def _set_picker_theme(self, theme: str) -> None:
        """Point the picker at the given theme without firing the change signal."""
        self.theme_combo.blockSignals(True)
        select_theme(self.theme_combo, theme)
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

    def _on_batch_finished(self, batch: QueuedBatch) -> None:
        """Show the changes of a finished batch and announce its result."""
        announce(self.batch_dock.batch_list, format_batch_row(batch))
        self.refresh_file_table()
        if self.detail_panel.current_file_id:
            self.detail_panel.select_file_asset(self.detail_panel.current_file_id)

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
        if not self._has_visible_files(ANALYZE_TITLE):
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

    def _has_visible_files(self, title: str) -> bool:
        """Return True with files visible. Otherwise say why the action did nothing.

        Each toolbar action works on the visible files. With none, the action ended
        without a word before.
        """
        if self.visible_file_ids:
            return True
        show_plain_message(self, QMessageBox.Icon.Information, title, NO_VISIBLE_FILES_TEXT)
        return False

    def _trigger_bulk_approve(self) -> None:
        """Bulk approve non-conflicting proposals for all visible files, then report the count."""
        if not self._has_visible_files(BULK_APPROVE_TITLE):
            return
        approved = self.review_service.bulk_approve_non_conflicting(self.visible_file_ids)
        self.refresh_file_table()
        if self.detail_panel.current_file_id:
            self.detail_panel.select_file_asset(self.detail_panel.current_file_id)
        show_plain_message(
            self,
            QMessageBox.Icon.Information,
            BULK_APPROVE_TITLE,
            BULK_APPROVE_RESULT_TEMPLATE.format(count=approved),
        )

    def _trigger_open_write_dialog(self) -> None:
        """Open write preview and execution dialog if callback is registered."""
        if self.on_open_write_dialog and self._has_visible_files(WRITE_TITLE):
            self.on_open_write_dialog(list(self.visible_file_ids))

    def _trigger_transcode_batch(self) -> None:
        """Invoke the batch transcode callback on visible files and refresh the table."""
        if self.on_transcode_batch and self._has_visible_files(TRANSCODE_TITLE):
            self.on_transcode_batch(list(self.visible_file_ids))
            self.refresh_file_table()

    def _trigger_export_playlist(self) -> None:
        """Ask for a .m3u8 destination and invoke the playlist export callback."""
        if not self.on_export_playlist or not self._has_visible_files(EXPORT_TITLE):
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
