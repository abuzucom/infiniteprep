"""Main application window for InfinitePrep (Section 9)."""

from __future__ import annotations

import base64
import binascii
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QByteArray, QMimeData, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDrag,
    QGuiApplication,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.config.settings import (
    SUPPORTED_UNSORTED_PLACEMENTS,
    TEXT_SIZE_STANDARD,
    UNSORTED_PLACEMENT_FIRST,
    AppSettings,
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
from infiniteprep.services.library_import import (
    LibraryImportRequest,
    LibraryImportResult,
    LibraryImportService,
    LocatedLibrary,
    PathMapping,
    Platform,
    find_installed_libraries,
)
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
from infiniteprep.ui.batch_queue_strip import BatchQueueStrip
from infiniteprep.ui.column_sizing import WeightedColumnSizer
from infiniteprep.ui.detail_panel import DetailPanel
from infiniteprep.ui.folder_import import FolderImportController, ImportReportDialog
from infiniteprep.ui.icon_loader import cached_themed_icon, themed_icon
from infiniteprep.ui.library_columns import (
    WARNING_ICON,
    ColumnVisibility,
    LibraryColumn,
    format_column_choices,
    format_status_cell,
    format_status_label,
    key_cell_style,
    parse_column_choices,
    status_icon,
)
from infiniteprep.ui.library_import_dialog import LibraryImportDialog
from infiniteprep.ui.library_sidebar import LibrarySidebar
from infiniteprep.ui.stylesheet import BUTTON_VARIANT_PROPERTY, ICON_BUTTON_VARIANT
from infiniteprep.ui.table_sort import (
    FILENAME_COLUMN,
    SORT_COLUMN_COUNT,
    LibraryRow,
    parse_bpm_value,
    sort_rows,
)
from infiniteprep.ui.theme import DEFAULT_DARK_THEME, THEME_PALETTES, resolve_theme
from infiniteprep.ui.time_format import split_time_seconds
from infiniteprep.ui.tokens import (
    DARK_PALETTE,
    STANDARD_TABLE_ROW_HEIGHT,
    Palette,
    StatusRole,
    scaled_size,
)
from infiniteprep.ui.workflow import (
    DETAIL_PANEL_WIDTH,
    MINIMUM_WINDOW_HEIGHT,
    MINIMUM_WINDOW_WIDTH,
    SIDEBAR_WIDTH,
    LayoutMode,
    LibraryFilter,
    WorkflowStage,
    count_stages,
    find_current_stage,
    find_layout_mode,
    fit_window_on_screens,
    matches_library_filter,
)
from infiniteprep.ui.workflow_bar import WorkflowBar, stage_shortcut
from infiniteprep.ui.write_preview_model import format_count

TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Filename",
    "Format",
    "Duration",
    "Artist",
    "Title",
    "Mix",
    "Label / Cat#",
    "BPM",
    "Key",
    "Status",
    "Issues",
)
MAIN_WINDOW_WIDTH: Final[int] = 1360
MAIN_WINDOW_HEIGHT: Final[int] = 820
if len(TABLE_COLUMNS) != SORT_COLUMN_COUNT or len(TABLE_COLUMNS) != len(LibraryColumn):
    raise RuntimeError("The library table columns and the sort kinds must stay aligned.")
WARNINGS_COLUMN: Final[int] = int(LibraryColumn.WARNINGS)
# Filename, Artist, and Title share the spare width equally. Every other column fits
# its cells.
LIBRARY_COLUMN_WEIGHTS: Final[dict[int, int]] = {
    LibraryColumn.FILENAME: 1,
    LibraryColumn.ARTIST: 1,
    LibraryColumn.TITLE: 1,
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


def format_warning_summary(warnings: list[str]) -> str:
    """Return the Issues cell text: a count such as "2 issues", or nothing for none."""
    if not warnings:
        return ""
    return format_count(len(warnings), "issue", "issues")


def format_approved_count(count: int) -> str:
    """Return the Approve all matches result, such as "Approved 3 fields."."""
    return f"Approved {format_count(count, 'field', 'fields')}."


def format_analyze_label(count: int) -> str:
    """Return the queue name of an Analyze batch, such as "Analyze 3 files"."""
    return f"Analyze {format_count(count, 'file', 'files')}"


# The status cell label of each review status value.
STATUS_FILTER_VALUES: Final[dict[str, str]] = {
    format_status_label(status): status.value for status in ReviewStatus
}
# Milliseconds after the last column resize before the layout saves.
LAYOUT_SAVE_DELAY_MS: Final[int] = 300
NO_VISIBLE_FILES_TEXT: Final[str] = "Nothing here yet. Add a folder or clear your filters."
ANALYZE_TITLE: Final[str] = "Analyze"
BULK_APPROVE_TITLE: Final[str] = "Approve all matches"
WRITE_TITLE: Final[str] = "Review changes"
TRANSCODE_TITLE: Final[str] = "Transcode"
EXPORT_TITLE: Final[str] = "Export playlist"
BATCH_RESULT_TITLE: Final[str] = "Results"
IMPORT_FAILED_TITLE: Final[str] = "Import failed"
CHOOSE_FOLDER_TITLE: Final[str] = "Choose a music folder"
PLAYLIST_FILE_FILTER: Final[str] = "Playlist (*.m3u8)"
PLAYLIST_DEFAULT_NAME: Final[str] = "crate.m3u8"
WINDOW_TITLE: Final[str] = "InfinitePrep"
SORT_ASCENDING_TEXT: Final[str] = "Ascending"
SORT_DESCENDING_TEXT: Final[str] = "Descending"
# Screen readers announce these names. Placeholder text vanishes once a field holds text.
SEARCH_INPUT_NAME: Final[str] = "Filter library"
SORT_COLUMN_NAME: Final[str] = "Sort library by"
SORT_DIRECTION_NAME: Final[str] = "Sort direction"
KEY_NOTATION_NAME: Final[str] = "Key notation"
FILE_TABLE_NAME: Final[str] = "Library"
SIDEBAR_TOGGLE_NAME: Final[str] = "Show sidebar"
SIDEBAR_TOGGLE_ICON: Final[str] = "folder"
SIDEBAR_TOGGLE_ICON_SIZE: Final[int] = 20
# A mnemonic label: the single ampersand marks the Alt key that focuses the picker.
KEY_NOTATION_LABEL: Final[str] = "&Key format:"
SETTINGS_SHORTCUT: Final[str] = "Ctrl+,"
# Space stays with the focused button or field, so Play/Pause takes Ctrl+P.
PLAY_PAUSE_SHORTCUT: Final[str] = "Ctrl+P"
MEDIA_PLAY_PAUSE_SHORTCUT: Final[str] = "Media Play/Pause"
PLAY_BUTTON_TOOLTIP_TEMPLATE: Final[str] = "Play or pause the preview ({shortcut})"
# Below the table, the detail panel starts at this height. Splitters reset each launch.
DETAIL_PANEL_HEIGHT: Final[int] = 420
PLAYLIST_SUFFIX: Final[str] = ".m3u8"
CLOSE_WAIT: Final[str] = "wait"
CLOSE_CANCEL: Final[str] = "cancel"
CLOSE_STAY: Final[str] = "stay"
IMPORT_FAILURE_TEXT: Final[str] = (
    "The import stopped and nothing was imported. Check the folder and try again."
)
CLOSE_PROMPT_TITLE: Final[str] = "Still working"
CLOSE_PROMPT_TEXT: Final[str] = (
    "You still have work running. If you quit now, it stops after the current file "
    "and anything waiting is dropped. A cancelled import saves nothing."
)
KEEP_WORKING_LABEL: Final[str] = "Keep working"
QUIT_NOW_LABEL: Final[str] = "Quit now"
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


def _current_import_platform() -> Platform:
    """Return the library locator Platform matching the host operating system."""
    if sys.platform.startswith("win"):
        return Platform.WINDOWS
    if sys.platform == "darwin":
        return Platform.MACOS
    return Platform.LINUX


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
        library_import_service: LibraryImportService | None = None,
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
        self.library_import_service = library_import_service
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

        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT)
        self.resize(MAIN_WINDOW_WIDTH, MAIN_WINDOW_HEIGHT)
        self._create_widgets(enable_audio_backend)
        self._setup_workflow_bar()
        self._setup_central_widget()
        self._setup_batch_queue()
        self._restore_table_layouts()
        self._apply_theme_palette()
        self.refresh_file_table()
        self._restore_window_geometry()
        self._apply_layout_mode(find_layout_mode(self.width()))

    def _create_widgets(self, enable_audio_backend: bool) -> None:
        """Create the search field, pickers, bar, sidebar, table, and detail panel."""
        self.search_input = QLineEdit(self)
        self.key_notation_combo = QComboBox(self)
        self.workflow_bar = WorkflowBar(self)
        self.sidebar = LibrarySidebar(self)
        self.layout_mode: LayoutMode | None = None
        self._library_folders: list[tuple[Path, int]] = []
        self.file_table = LibraryTableWidget(self)
        self.file_table.setColumnCount(len(TABLE_COLUMNS))
        self.detail_panel = DetailPanel(
            repository=self.repo,
            review_service=self.review_service,
            on_change_callback=self.refresh_file_table,
            parent=self,
            enable_audio_backend=enable_audio_backend,
        )

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
        # The strip shows progress. The full queue opens above it on a click.
        self.batch_dock.hide()
        self.batch_strip = BatchQueueStrip(self.batch_queue, self)
        self.centralWidget().layout().addWidget(self.batch_strip)
        self.batch_strip.queue_toggled.connect(self.batch_dock.setVisible)
        self.batch_dock.visibilityChanged.connect(self.batch_strip.set_queue_shown)
        self._close_after_queue = False
        self.import_failure_box: QMessageBox | None = None
        self.batch_result_box: QMessageBox | None = None
        self.batch_queue.batch_finished.connect(self._on_batch_finished)
        self.batch_queue.changed.connect(self._update_action_locks)
        self.batch_queue.idle.connect(self._on_queue_idle)
        self.batch_dock.result_requested.connect(self.show_batch_result)

    def _setup_workflow_bar(self) -> None:
        """Connect the workflow bar and the sidebar to the stage actions and tools.

        The bulk_btn, transcode_btn, export_playlist_btn, and journal_btn attributes
        name the buttons that run those actions, for the batch locks and the callers.
        """
        self._stage_actions: dict[WorkflowStage, Callable[[], None]] = {
            WorkflowStage.IMPORT: self._prompt_import_folder,
            WorkflowStage.ANALYZE: self._trigger_analyze_all,
            WorkflowStage.REVIEW: self._trigger_bulk_approve,
            WorkflowStage.APPLY: self._trigger_open_write_dialog,
            WorkflowStage.EXPORT: self._trigger_export_playlist,
        }
        bar = self.workflow_bar
        bar.stage_triggered.connect(self._on_stage_triggered)
        bar.import_library_requested.connect(self._trigger_import_library)
        bar.transcode_requested.connect(self._trigger_transcode_batch)
        bar.history_requested.connect(self._trigger_open_journal)
        bar.settings_requested.connect(self._trigger_open_settings)
        self.sidebar.stage_triggered.connect(self._on_stage_triggered)
        self.sidebar.filter_changed.connect(self._on_library_filter_changed)
        self.bulk_btn = bar.stage_buttons[WorkflowStage.REVIEW]
        self.export_playlist_btn = bar.stage_buttons[WorkflowStage.EXPORT]
        self.import_library_btn = bar.import_library_btn
        self.transcode_btn = bar.transcode_btn
        self.journal_btn = bar.history_btn
        for notation in KeyDisplayNotation:
            self.key_notation_combo.addItem(KEY_NOTATION_LABELS[notation], notation.value)
        self.key_notation_combo.setCurrentIndex(
            self.key_notation_combo.findData(self.current_key_notation.value)
        )
        self.key_notation_combo.currentIndexChanged.connect(self._on_key_notation_picked)
        name_combo_box(self.key_notation_combo, KEY_NOTATION_NAME)
        self._setup_shortcuts()

    def _on_stage_triggered(self, stage_value: str) -> None:
        """Run the action of the stage that a button or Alt shortcut names, unless locked.

        The buttons of a locked stage are disabled. The Alt shortcut reaches this method
        anyway, so the lock check sits here too.
        """
        stage = WorkflowStage(stage_value)
        if self.workflow_bar.is_stage_locked(stage):
            return
        self._stage_actions[stage]()

    def _on_library_filter_changed(self, _filter_value: str) -> None:
        """Show the tracks of the new filter and close the sidebar drawer."""
        if self.layout_mode is not LayoutMode.WIDE:
            self.sidebar.hide()
        self.refresh_file_table()

    def _setup_shortcuts(self) -> None:
        """Bind window-wide keys for Import, Settings, and preview Play/Pause."""
        context = Qt.ShortcutContext.WindowShortcut
        self.import_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Open), self)
        self.import_shortcut.setContext(context)
        self.import_shortcut.activated.connect(self._prompt_import_folder)
        self.settings_shortcut = QShortcut(QKeySequence(SETTINGS_SHORTCUT), self)
        self.settings_shortcut.setContext(context)
        self.settings_shortcut.activated.connect(self._trigger_open_settings)
        # Alt and the stage number run that stage, as the number on the bar shows.
        self.stage_shortcuts: dict[WorkflowStage, QShortcut] = {}
        for stage in WorkflowStage:
            shortcut = QShortcut(stage_shortcut(stage), self)
            shortcut.setContext(context)
            shortcut.activated.connect(lambda chosen=stage: self._on_stage_triggered(chosen.value))
            self.stage_shortcuts[stage] = shortcut
        # Both keys press the player button, so a new selection loads before it plays.
        play_button = self.detail_panel.audio_player.play_btn
        self.play_pause_shortcut = QShortcut(QKeySequence(PLAY_PAUSE_SHORTCUT), self)
        self.media_play_pause_shortcut = QShortcut(QKeySequence(MEDIA_PLAY_PAUSE_SHORTCUT), self)
        for shortcut in (self.play_pause_shortcut, self.media_play_pause_shortcut):
            shortcut.setContext(context)
            shortcut.activated.connect(play_button.click)
        shown_key = self.play_pause_shortcut.key().toString(QKeySequence.SequenceFormat.NativeText)
        play_button.setToolTip(PLAY_BUTTON_TOOLTIP_TEMPLATE.format(shortcut=shown_key))

    def _setup_central_widget(self) -> None:
        """Build the workflow bar, the sidebar, the filter row, the table, and the panel."""
        central = QWidget(self)
        root_layout = QVBoxLayout(central)
        root_layout.addWidget(self.workflow_bar)
        content = QWidget(central)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addLayout(self._build_filter_row())
        self._setup_file_table()
        self.detail_splitter = QSplitter(Qt.Orientation.Horizontal, content)
        self.detail_splitter.addWidget(self.file_table)
        # The panel's contents need more than 480 px. The scroll area lets the splitter
        # hold the panel at its set size, and the contents scroll inside it.
        self.detail_scroll = QScrollArea(content)
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # The panel's own controls take Tab. The scroll area follows the focused control.
        self.detail_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.detail_scroll.setWidget(self.detail_panel)
        self.detail_splitter.addWidget(self.detail_scroll)
        content_layout.addWidget(self.detail_splitter)
        self.body_splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self.body_splitter.addWidget(self.sidebar)
        self.body_splitter.addWidget(content)
        root_layout.addWidget(self.body_splitter)
        self.setCentralWidget(central)

    def _build_filter_row(self) -> QHBoxLayout:
        """Return the row above the table: sidebar toggle, search, key notation, and sort."""
        filter_bar = QHBoxLayout()
        toggle = QToolButton(self)
        toggle.setProperty(BUTTON_VARIANT_PROPERTY, ICON_BUTTON_VARIANT)
        toggle.setIconSize(QSize(SIDEBAR_TOGGLE_ICON_SIZE, SIDEBAR_TOGGLE_ICON_SIZE))
        toggle.setToolTip(SIDEBAR_TOGGLE_NAME)
        toggle.setAccessibleName(SIDEBAR_TOGGLE_NAME)
        toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        toggle.clicked.connect(self._toggle_sidebar_drawer)
        filter_bar.addWidget(toggle)
        self.sidebar_toggle_btn = toggle
        self.search_input.setPlaceholderText("Filter by filename, artist, title, or label...")
        self.search_input.textChanged.connect(self.refresh_file_table)
        self.search_input.setAccessibleName(SEARCH_INPUT_NAME)
        filter_bar.addWidget(self.search_input, stretch=1)
        key_label = QLabel(KEY_NOTATION_LABEL, self)
        key_label.setBuddy(self.key_notation_combo)
        filter_bar.addWidget(key_label)
        filter_bar.addWidget(self.key_notation_combo)
        self._add_sort_controls(filter_bar)
        return filter_bar

    def _setup_file_table(self) -> None:
        """Set the headers, sizing, sorting, selection, and actions of the library table."""
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
        self._setup_column_visibility()

    def _setup_column_visibility(self) -> None:
        """Let columns move, hide them by width and by header menu choice, and save both."""
        header = self.file_table.horizontalHeader()
        header.setSectionsMovable(True)
        stored = getattr(SettingsManager().load(), "file_table_column_choices", "")
        self.column_visibility = ColumnVisibility(
            self.file_table, TABLE_COLUMNS, parse_column_choices(stored)
        )
        self.column_visibility.changed.connect(self._on_column_choices_changed)

    def _on_column_choices_changed(self) -> None:
        """Store the header menu choices and refit the columns that still show."""
        settings_manager = SettingsManager()
        settings = settings_manager.load()
        settings.file_table_column_choices = format_column_choices(self.column_visibility.choices)
        settings_manager.save(settings)
        self.file_sizer.refit()
        self._schedule_layout_save()

    def _apply_layout_mode(self, mode: LayoutMode) -> None:
        """Arrange the bar, the sidebar, and the detail panel for a window width band.

        From 1600 px the sidebar stays open and the bar shows every label. Below that
        the sidebar opens as a drawer from the toggle button. Below 1280 px the detail
        panel moves under the table. Each change resets the splitters.
        """
        if mode is self.layout_mode:
            return
        self.layout_mode = mode
        wide = mode is LayoutMode.WIDE
        self.workflow_bar.set_compact(not wide)
        self.sidebar_toggle_btn.setVisible(not wide)
        if wide:
            self.body_splitter.insertWidget(0, self.sidebar)
            self.sidebar.show()
        else:
            self.sidebar.setParent(self.centralWidget())
            self.sidebar.hide()
        orientation = (
            Qt.Orientation.Vertical if mode is LayoutMode.NARROW else Qt.Orientation.Horizontal
        )
        self.detail_splitter.setOrientation(orientation)
        # The splitters learn their new size only after this resize event, so the reset
        # waits for the layout pass. The table takes the stretch, so the panel keeps its size.
        if wide:
            self.body_splitter.setStretchFactor(0, 0)
            self.body_splitter.setStretchFactor(1, 1)
        self.detail_splitter.setStretchFactor(0, 1)
        self.detail_splitter.setStretchFactor(1, 0)
        QTimer.singleShot(0, self._reset_splitter_sizes)

    def _reset_splitter_sizes(self) -> None:
        """Give the sidebar and the detail panel their starting sizes for the layout."""
        if self.layout_mode is LayoutMode.WIDE:
            content_width = max(0, self.body_splitter.width() - SIDEBAR_WIDTH)
            self.body_splitter.setSizes([SIDEBAR_WIDTH, content_width])
        if self.detail_splitter.orientation() == Qt.Orientation.Vertical:
            table_height = max(0, self.detail_splitter.height() - DETAIL_PANEL_HEIGHT)
            self.detail_splitter.setSizes([table_height, DETAIL_PANEL_HEIGHT])
            return
        table_width = max(0, self.detail_splitter.width() - DETAIL_PANEL_WIDTH)
        self.detail_splitter.setSizes([table_width, DETAIL_PANEL_WIDTH])

    def _toggle_sidebar_drawer(self) -> None:
        """Open the sidebar over the content as a drawer, or close an open drawer."""
        if not self.sidebar.isHidden():
            self.sidebar.hide()
            return
        self._place_sidebar_drawer()
        self.sidebar.show()
        self.sidebar.raise_()

    def _place_sidebar_drawer(self) -> None:
        """Put the drawer at the left edge of the content, from top to bottom."""
        area = self.body_splitter.geometry()
        self.sidebar.setGeometry(area.x(), area.y(), SIDEBAR_WIDTH, area.height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Switch the layout when the window width crosses a breakpoint."""
        super().resizeEvent(event)
        self._apply_layout_mode(find_layout_mode(self.width()))
        if self.layout_mode is not LayoutMode.WIDE and not self.sidebar.isHidden():
            self._place_sidebar_drawer()

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
            # A saved layout keeps every width as saved.
            if saved_layout_fits and self._restore_header_state(header, encoded_state):
                sizer.keep_restored_widths()
            header.sectionResized.connect(self._schedule_layout_save)
        # A dragged column order persists the same way as a dragged width.
        self.file_table.horizontalHeader().sectionMoved.connect(self._schedule_layout_save)
        # The width rules wait for the table's first resize. Before the window shows, the
        # table holds a default size, and the rules would hide columns at that size.

    def _schedule_layout_save(self, *_args: int) -> None:
        """Restart the layout save timer after a column resize, move, or hide."""
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
        self._copy_table_layouts(settings)
        settings_manager.save(settings)

    def _save_close_state(self) -> None:
        """Save the window geometry and any pending column layout in one write."""
        layout_pending = self._layout_save_timer.isActive()
        self._layout_save_timer.stop()
        settings_manager = SettingsManager()
        settings = settings_manager.load()
        if layout_pending:
            self._copy_table_layouts(settings)
        settings.window_geometry = base64.b64encode(bytes(self.saveGeometry())).decode("ascii")
        settings_manager.save(settings)

    def _copy_table_layouts(self, settings: AppSettings) -> None:
        """Copy the file and detail header states into a settings value."""
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

    def refresh_file_table(self) -> None:
        """Reload all FileAssets from Repository and populate the filtered table."""
        all_files = self.repo.list_files()
        snapshot = load_library_snapshot(self.repo)
        self._show_library_counts(all_files)
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

    def _show_library_counts(self, all_files: list[FileAsset]) -> None:
        """Show the stage counts, the current stage, and the source folders.

        The counts cover the whole library, whatever filter or search applies.
        """
        counts = count_stages(str(asset.review_status) for asset in all_files)
        current = find_current_stage(counts)
        self.workflow_bar.set_counts(counts, current)
        self.sidebar.set_counts(counts, current)
        folder_counts = Counter(Path(asset.current_path).parent for asset in all_files)
        folders = sorted(folder_counts.items(), key=lambda entry: str(entry[0]).casefold())
        if folders != self._library_folders:
            self._library_folders = folders
            self.sidebar.set_folders(folders)

    def _apply_theme_palette(self) -> None:
        """Use the stored theme's colors for icons and the stored text size for table rows."""
        settings = SettingsManager().load()
        stored_theme = getattr(settings, "theme", DEFAULT_DARK_THEME)
        palette: Palette = THEME_PALETTES.get(resolve_theme(stored_theme), DARK_PALETTE)
        self._palette = palette
        text_size = getattr(settings, "text_size", TEXT_SIZE_STANDARD)
        row_height = scaled_size(STANDARD_TABLE_ROW_HEIGHT, text_size)
        rows = self.file_table.verticalHeader()
        rows.setMinimumSectionSize(min(rows.minimumSectionSize(), row_height))
        rows.setDefaultSectionSize(row_height)
        self.workflow_bar.set_palette(palette)
        self.sidebar.set_palette(palette)
        self.detail_panel.set_palette(palette)
        self.sidebar_toggle_btn.setIcon(themed_icon(SIDEBAR_TOGGLE_ICON, palette.text))

    def _restore_window_geometry(self) -> None:
        """Restore the saved window size and position, on a screen that still exists."""
        encoded = getattr(SettingsManager().load(), "window_geometry", "")
        if not encoded or not isinstance(encoded, str):
            return
        try:
            state = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return
        if not self.restoreGeometry(QByteArray(state)):
            return
        screens = [screen.availableGeometry() for screen in QGuiApplication.screens()]
        self.setGeometry(fit_window_on_screens(self.geometry(), screens))

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

        The Settings dialog applies a theme itself. The icons follow the stored theme.
        """
        self._unsorted_placement = self._read_unsorted_placement()
        self._apply_theme_palette()
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
        """Return True if the asset passes the library filter checked in the sidebar."""
        chosen: LibraryFilter = self.sidebar.current_filter()
        # Only the Has warnings filter reads the warnings, so other filters skip the work.
        has_warnings = chosen is LibraryFilter.HAS_WARNINGS and bool(
            self._asset_warnings(asset, snapshot)
        )
        return matches_library_filter(chosen, str(asset.review_status), has_warnings)

    def _populate_table_row(self, row_index: int, row: LibraryRow) -> None:
        """Render one library row and store its file ID on the first cell."""
        for column_index, text in enumerate(row.cells):
            item = QTableWidgetItem(text)
            if column_index == 0:
                item.setData(Qt.ItemDataRole.UserRole, row.file_id)
            if column_index == WARNINGS_COLUMN and row.warning_details:
                item.setToolTip(row.warning_details)
                warning_color = self._palette.status[StatusRole.WARNING]
                item.setIcon(cached_themed_icon(WARNING_ICON, warning_color))
            if column_index == LibraryColumn.STATUS:
                self._show_status_icon(item, row)
            if column_index == LibraryColumn.KEY:
                self._color_key_cell(item, row)
            self.file_table.setItem(row_index, column_index, item)

    def _show_status_icon(self, item: QTableWidgetItem, row: LibraryRow) -> None:
        """Show the status as an icon. The label stays as tooltip and spoken text.

        The search reads the row cells, so a search for a status still finds the row.
        """
        state = status_icon(row.review_status, self._palette)
        if state is None:
            return
        label = item.text()
        icon_name, color = state
        item.setIcon(cached_themed_icon(icon_name, color))
        item.setText("")
        item.setToolTip(label)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, label)

    @staticmethod
    def _color_key_cell(item: QTableWidgetItem, row: LibraryRow) -> None:
        """Fill a key cell with its Open Key color and the text color that reads on it."""
        style = key_cell_style(row.key)
        if style is None:
            return
        item.setBackground(QColor(style.fill))
        item.setForeground(QColor(style.text))

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

    def _prompt_import_folder(self) -> None:
        """Open a folder picker and import the chosen folder in the background."""
        chosen = QFileDialog.getExistingDirectory(self, CHOOSE_FOLDER_TITLE)
        if chosen:
            self.start_folder_import(Path(chosen))

    def _trigger_import_library(self) -> None:
        """Open the three-step DJ library import dialog and commit accepted changes."""
        service = self.library_import_service or LibraryImportService(self.repo)
        libraries = find_installed_libraries(Path.home(), _current_import_platform())

        def build_plan(
            library: LocatedLibrary, mappings: list[PathMapping], root_text: str
        ) -> LibraryImportResult:
            request = LibraryImportRequest(
                source_path=library.file_path,
                format_id=library.format_id,
                root=Path(root_text),
                mappings=tuple(mappings),
            )
            return service.build_plan(request)

        dialog = LibraryImportDialog(
            libraries,
            self,
            readable_formats=service.registry.format_ids(),
            plan_builder=build_plan,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.pending_result()
        if result is not None:
            service.commit(result)
            self.refresh_file_table()

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
        # Review runs Approve all matches, so its bar, primary, and sidebar buttons wait.
        self.workflow_bar.set_stages_locked(set() if idle else {WorkflowStage.REVIEW})
        for widget in (
            self.sidebar.stage_buttons[WorkflowStage.REVIEW],
            self.import_library_btn,
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
            BATCH_RESULT_TITLE,
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
            IMPORT_FAILED_TITLE,
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
        box.setWindowTitle(CLOSE_PROMPT_TITLE)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(CLOSE_PROMPT_TEXT)
        box.addButton(KEEP_WORKING_LABEL, QMessageBox.ButtonRole.RejectRole)
        cancel_button = box.addButton(QUIT_NOW_LABEL, QMessageBox.ButtonRole.DestructiveRole)
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
            self._save_close_state()
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
                format_analyze_label(len(file_ids)),
                lambda should_cancel, progress: runner(file_ids, should_cancel, progress),
            )
            return
        if self.on_analyze_batch is not None:
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
            format_approved_count(approved),
        )

    def _trigger_open_write_dialog(self) -> None:
        """Open write preview and execution dialog if callback is registered."""
        if self.on_open_write_dialog is not None and self._has_visible_files(WRITE_TITLE):
            self.on_open_write_dialog(list(self.visible_file_ids))

    def _trigger_transcode_batch(self) -> None:
        """Invoke the batch transcode callback on visible files and refresh the table."""
        if self.on_transcode_batch is not None and self._has_visible_files(TRANSCODE_TITLE):
            self.on_transcode_batch(list(self.visible_file_ids))
            self.refresh_file_table()

    def _trigger_export_playlist(self) -> None:
        """Ask for a .m3u8 destination and invoke the playlist export callback."""
        if self.on_export_playlist is None or not self._has_visible_files(EXPORT_TITLE):
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self, EXPORT_TITLE, PLAYLIST_DEFAULT_NAME, PLAYLIST_FILE_FILTER
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
        if self.on_open_journal_dialog is not None:
            self.on_open_journal_dialog()

    def _trigger_open_settings(self) -> None:
        """Open provider and setup settings dialog if callback is registered."""
        if self.on_open_settings_dialog is not None:
            self.on_open_settings_dialog()
