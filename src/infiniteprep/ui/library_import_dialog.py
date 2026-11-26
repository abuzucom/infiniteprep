"""The three-step dialog that imports a DJ library.

Step one lists the installed libraries. The DJ picks one. Step two maps the
library paths onto this computer. Step three shows the conflicts and commits
the import. Every path and version comes from outside the app, so every one
renders as plain text.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.services.library_import.format_detection import detect_format
from infiniteprep.services.library_import.importer import NoImporterError, production_registry
from infiniteprep.services.library_import.locations import format_display_name
from infiniteprep.services.library_import.locator import LocatedLibrary, read_library_version
from infiniteprep.services.library_import.path_mapping import PathMapping
from infiniteprep.services.library_import.service import LibraryImportResult
from infiniteprep.ui.action_handlers import show_plain_message
from infiniteprep.ui.library_import_model import (
    ADD_MAPPING_BUTTON,
    BACK_BUTTON,
    CANCEL_BUTTON,
    CHOOSE_FILE_BUTTON,
    COMMIT_BUTTON,
    CONFLICT_LABEL,
    EMPTY_CONFLICT_TEXT,
    EMPTY_LIST_TEXT,
    FORMAT_LABEL,
    MODIFIED_LABEL,
    NEXT_BUTTON,
    PATH_LABEL,
    PENDING_LABEL,
    REMOVE_MAPPING_BUTTON,
    ROOT_LABEL,
    ROOT_PLACEHOLDER,
    SOURCE_PREFIX_LABEL,
    TARGET_PREFIX_LABEL,
    UNRESOLVED_LABEL,
    VERSION_LABEL,
    WINDOW_TITLE,
    format_conflict,
    format_pending,
    format_timestamp,
    format_version,
)

DIALOG_WIDTH: Final[int] = 760
DIALOG_HEIGHT: Final[int] = 620
STEP_FIND: Final[int] = 0
STEP_MAP: Final[int] = 1
STEP_REVIEW: Final[int] = 2
MAPPING_COLUMNS: Final[int] = 2
COLUMN_SOURCE: Final[int] = 0
COLUMN_TARGET: Final[int] = 1


PlanBuilder = Callable[[LocatedLibrary, list[PathMapping], str], LibraryImportResult]


class LibraryImportDialog(QDialog):
    """Modal dialog that finds a DJ library and imports it.

    The dialog holds the pending import. The caller reads the chosen library,
    the saved mappings, and the conflict list after the dialog closes.
    """

    import_requested = Signal()

    def __init__(
        self,
        libraries: list[LocatedLibrary],
        parent: QWidget | None = None,
        readable_formats: Iterable[str] | None = None,
        plan_builder: PlanBuilder | None = None,
    ) -> None:
        """Build the dialog from the libraries the locator found.

        readable_formats names the formats the app can read. A library of
        another format stays out of the list, so the dialog never offers an
        import the app cannot carry out. The formats of the production
        registry are the ones the app can read, so a caller that names none
        gets the formats the app holds a reader for.
        """
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(DIALOG_WIDTH, DIALOG_HEIGHT)
        self._libraries = libraries
        self._readable_formats = (
            set(readable_formats)
            if readable_formats is not None
            else set(production_registry().format_ids())
        )
        self._plan_builder = plan_builder
        self._pending_result: LibraryImportResult | None = None
        self._shown: list[LocatedLibrary] = []
        self._chosen: LocatedLibrary | None = None

        self.found_list = QListWidget(self)
        self.found_details = QLabel(self)
        self.choose_file_btn = QPushButton(CHOOSE_FILE_BUTTON, self)
        self.mappings_table = QTableWidget(self)
        self.root_edit = QLineEdit(self)
        self.add_mapping_btn = QPushButton(ADD_MAPPING_BUTTON, self)
        self.remove_mapping_btn = QPushButton(REMOVE_MAPPING_BUTTON, self)
        self.conflicts_list = QListWidget(self)
        self.pending_list = QListWidget(self)
        self.unresolved_list = QListWidget(self)
        self.next_btn = QPushButton(NEXT_BUTTON, self)
        self.back_btn = QPushButton(BACK_BUTTON, self)
        self.commit_btn = QPushButton(COMMIT_BUTTON, self)
        self.cancel_btn = QPushButton(CANCEL_BUTTON, self)
        self.steps = QStackedWidget(self)

        self._fill_found_list()
        self._build_layout()
        self._apply_step_state(STEP_FIND)

    def chosen_library(self) -> LocatedLibrary | None:
        """Return the library the DJ picked, or None when none was picked."""
        return self._chosen

    def saved_mappings(self) -> list[PathMapping]:
        """Return the prefix mappings the DJ entered."""
        mappings: list[PathMapping] = []
        for row in range(self.mappings_table.rowCount()):
            source = self._cell_text(row, COLUMN_SOURCE)
            target = self._cell_text(row, COLUMN_TARGET)
            if not source or not target:
                continue
            mappings.append(PathMapping(source_prefix=source, target_prefix=target))
        return mappings

    def chosen_root(self) -> str:
        """Return the folder every imported file must sit inside."""
        return self.root_edit.text().strip()

    def pending_result(self) -> LibraryImportResult | None:
        """Return the built import plan waiting for commit, or None."""
        return self._pending_result

    def set_conflicts(self, conflict_lines: list[str]) -> None:
        """Show the conflicts the merge found."""
        self.conflicts_list.clear()
        self.conflicts_list.addItems(conflict_lines or [EMPTY_CONFLICT_TEXT])

    def set_pending(self, pending_lines: list[str]) -> None:
        """Show the values that wait for their storage."""
        self.pending_list.clear()
        self.pending_list.addItems(pending_lines)

    def set_unresolved(self, unresolved_lines: list[str]) -> None:
        """Show the files the search did not find."""
        self.unresolved_list.clear()
        self.unresolved_list.addItems(unresolved_lines)

    def _fill_found_list(self) -> None:
        """Add one row per located library the app can read."""
        self._shown = [library for library in self._libraries if self._can_read(library)]
        if not self._shown:
            self.found_list.addItem(EMPTY_LIST_TEXT)
            return
        for library in self._shown:
            self.found_list.addItem(library.file_path.name)

    def _can_read(self, library: LocatedLibrary) -> bool:
        """Return True when the app holds a reader for the format of a library."""
        return library.format_id in self._readable_formats

    def _build_layout(self) -> None:
        """Assemble the three steps and the buttons."""
        root = QVBoxLayout(self)
        root.addWidget(self.steps)
        self.steps.addWidget(self._build_find_step())
        self.steps.addWidget(self._build_map_step())
        self.steps.addWidget(self._build_review_step())
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.back_btn)
        buttons.addWidget(self.next_btn)
        buttons.addWidget(self.commit_btn)
        buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)
        self.next_btn.clicked.connect(self._go_next)
        self.back_btn.clicked.connect(self._go_back)
        self.commit_btn.clicked.connect(self._commit)
        self.cancel_btn.clicked.connect(self.reject)
        self.choose_file_btn.clicked.connect(self._choose_file)
        self.add_mapping_btn.clicked.connect(self._add_mapping)
        self.remove_mapping_btn.clicked.connect(self._remove_mapping)
        self.found_list.currentRowChanged.connect(self._on_found_row_changed)
        self.cancel_btn.setDefault(True)
        self.commit_btn.setAutoDefault(False)

    def _build_find_step(self) -> QWidget:
        """Return the step that lists the installed libraries."""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Installed DJ libraries", page))
        layout.addWidget(self.found_list)
        self.found_details.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.found_details)
        layout.addWidget(self.choose_file_btn)
        return page

    def _build_map_step(self) -> QWidget:
        """Return the step that maps the library paths."""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Map the library paths onto this computer", page))
        self.mappings_table.setColumnCount(MAPPING_COLUMNS)
        self.mappings_table.setHorizontalHeaderLabels([SOURCE_PREFIX_LABEL, TARGET_PREFIX_LABEL])
        layout.addWidget(self.mappings_table)
        mapping_buttons = QHBoxLayout()
        mapping_buttons.addWidget(self.add_mapping_btn)
        mapping_buttons.addWidget(self.remove_mapping_btn)
        mapping_buttons.addStretch()
        layout.addLayout(mapping_buttons)
        root_row = QHBoxLayout()
        root_label = QLabel(ROOT_LABEL, page)
        self.root_edit.setPlaceholderText(ROOT_PLACEHOLDER)
        root_row.addWidget(root_label)
        root_row.addWidget(self.root_edit)
        layout.addLayout(root_row)
        return page

    def _build_review_step(self) -> QWidget:
        """Return the step that shows the conflicts."""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(self._titled_group(CONFLICT_LABEL, self.conflicts_list, page))
        layout.addWidget(self._titled_group(PENDING_LABEL, self.pending_list, page))
        layout.addWidget(self._titled_group(UNRESOLVED_LABEL, self.unresolved_list, page))
        return page

    def _titled_group(self, title: str, widget: QListWidget, page: QWidget) -> QGroupBox:
        """Return a titled group holding one list."""
        group = QGroupBox(title, page)
        layout = QVBoxLayout(group)
        layout.addWidget(widget)
        return group

    def _on_found_row_changed(self, row: int) -> None:
        """Show the details of the picked library."""
        if row < 0 or row >= len(self._shown):
            self._chosen = None
            self.found_details.setText("")
            return
        self._chosen = self._shown[row]
        self.found_details.setText(self._details_text(self._chosen))

    def _details_text(self, library: LocatedLibrary) -> str:
        """Return the detail lines of one located library."""
        lines = [
            f"{FORMAT_LABEL}: {format_display_name(library.format_id)}",
            f"{VERSION_LABEL}: {format_version(library.version)}",
            f"{MODIFIED_LABEL}: {format_timestamp(library.modified_at)}",
            f"{PATH_LABEL}: {library.file_path}",
        ]
        return "\n".join(lines)

    def _choose_file(self) -> None:
        """Let the DJ name a library file by hand.

        The chosen file becomes a located library, so the import reads the
        file the DJ picked.
        """
        chosen, _filter = QFileDialog.getOpenFileName(self, WINDOW_TITLE)
        if not chosen:
            return
        manual = _manual_library(Path(chosen))
        self._libraries = [*self._libraries, manual]
        self.found_list.clear()
        self._shown = [library for library in self._libraries if self._can_read(library)]
        for library in self._shown:
            self.found_list.addItem(library.file_path.name)
        self.found_list.setCurrentRow(len(self._shown) - 1)

    def _add_mapping(self) -> None:
        """Add an empty mapping row."""
        row = self.mappings_table.rowCount()
        self.mappings_table.insertRow(row)
        for column in range(MAPPING_COLUMNS):
            self.mappings_table.setItem(row, column, QTableWidgetItem(""))

    def _remove_mapping(self) -> None:
        """Remove the selected mapping row."""
        row = self.mappings_table.currentRow()
        if row >= 0:
            self.mappings_table.removeRow(row)

    def _cell_text(self, row: int, column: int) -> str:
        """Return the plain text of one table cell."""
        item = self.mappings_table.item(row, column)
        return item.text() if item else ""

    def _go_next(self) -> None:
        """Move to the next step, building the review plan before Review."""
        current = self.steps.currentIndex()
        if current == STEP_FIND:
            self.steps.setCurrentIndex(STEP_MAP)
        elif current == STEP_MAP:
            if self._plan_builder is not None and not self._build_review_plan():
                return
            self.steps.setCurrentIndex(STEP_REVIEW)
        self._apply_step_state(self.steps.currentIndex())

    def _build_review_plan(self) -> bool:
        """Build the pending plan and fill the Review lists, or warn on error."""
        if self._chosen is None:
            show_plain_message(
                self, QMessageBox.Icon.Warning, WINDOW_TITLE, "Pick a library first."
            )
            return False
        root_text = self.chosen_root()
        if not root_text:
            show_plain_message(
                self, QMessageBox.Icon.Warning, WINDOW_TITLE, "Choose a music root folder first."
            )
            return False
        assert self._plan_builder is not None
        try:
            result = self._plan_builder(self._chosen, self.saved_mappings(), root_text)
        except NoImporterError as exc:
            show_plain_message(self, QMessageBox.Icon.Warning, WINDOW_TITLE, str(exc))
            return False
        if result.error:
            show_plain_message(self, QMessageBox.Icon.Warning, WINDOW_TITLE, result.error)
            return False
        self.set_conflicts(
            [
                format_conflict_line(c.field_name, c.imported_value, c.stored_value)
                for c in result.conflicts
            ]
        )
        self.set_pending([format_pending_line(name) for name in result.pending_fields])
        self.set_unresolved(list(result.unresolved_paths))
        self._pending_result = result
        return True

    def _go_back(self) -> None:
        """Move to the previous step and discard any unapplied review plan."""
        if self.steps.currentIndex() == STEP_REVIEW:
            self._pending_result = None
        self.steps.setCurrentIndex(max(0, self.steps.currentIndex() - 1))
        self._apply_step_state(self.steps.currentIndex())

    def _commit(self) -> None:
        """Accept the dialog and report that the DJ asked for the import.

        The caller commits the pending changes and reports the outcome. The
        dialog never claims the import finished.
        """
        self.import_requested.emit()
        self.accept()

    def _apply_step_state(self, step: int) -> None:
        """Show the buttons that fit the current step."""
        self.back_btn.setVisible(step != STEP_FIND)
        self.next_btn.setVisible(step != STEP_REVIEW)
        self.commit_btn.setVisible(step == STEP_REVIEW)


def format_conflict_line(field_name: str, imported_value: str, stored_value: str) -> str:
    """Return the line of one conflict."""
    return format_conflict(field_name, imported_value, stored_value)


def _manual_library(file_path: Path) -> LocatedLibrary:
    """Return the located library of a file the DJ named by hand.

    The framework reads the header of the file to name the format, so the
    registry can pick the reader that goes with it. The version comes from
    the same header.
    """
    return LocatedLibrary(
        format_id=detect_format(file_path),
        file_path=file_path,
        folder_path=file_path.parent,
        version=read_library_version(file_path),
        modified_at=_modified_time(file_path),
        observed=True,
        citation_url="",
        citation_note="",
    )


def _modified_time(file_path: Path) -> float | None:
    """Return the last-modified time of the file, or None when it cannot be read."""
    try:
        return file_path.stat().st_mtime
    except OSError:
        return None


def format_pending_line(field_name: str) -> str:
    """Return the line of one value that waits for its storage."""
    return format_pending(field_name)
