"""The sidebar: workflow stages, library filters, source folders, and planned lists."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.ui.icon_loader import themed_icon
from infiniteprep.ui.stylesheet import CAPTION_TEXT_ROLE, CURRENT_STAGE_PROPERTY, TEXT_ROLE_PROPERTY
from infiniteprep.ui.tokens import DARK_PALETTE, Palette
from infiniteprep.ui.workflow import STAGE_ORDER, LibraryFilter, WorkflowStage
from infiniteprep.ui.workflow_bar import STAGE_ICONS, STAGE_LABELS
from infiniteprep.ui.write_preview_model import format_count

WORKFLOW_HEADING: Final[str] = "Workflow"
LIBRARY_HEADING: Final[str] = "Library"
FOLDERS_HEADING: Final[str] = "Folders"
LISTS_HEADING: Final[str] = "Playlists and crates"
FILTER_LABELS: Final[dict[LibraryFilter, str]] = {
    LibraryFilter.ALL: "All tracks",
    LibraryFilter.NEEDS_REVIEW: "Needs review",
    LibraryFilter.HAS_WARNINGS: "With issues",
    LibraryFilter.READY_TO_WRITE: "Ready to save",
}
NO_FOLDERS_TEXT: Final[str] = "No folders yet"
PLANNED_LISTS: Final[tuple[str, ...]] = ("Playlists (coming later)", "Crates (coming later)")
SIDEBAR_ICON_SIZE: Final[int] = 16
SIDEBAR_MIN_WIDTH: Final[int] = 160
SIDEBAR_NAME: Final[str] = "Sidebar"


def format_folder_row(folder: Path, count: int) -> str:
    """Return the text of one folder row: the folder name and its track count."""
    name = folder.name or str(folder)
    return f"{name}  {count}"


class LibrarySidebar(QScrollArea):
    """Sidebar with the stages, the library filters, the folders, and planned lists.

    A stage button runs its stage's action, as in the workflow bar. The filter
    buttons pick which tracks the table shows. Exactly one filter stays checked.
    A folder row narrows the table to that folder. A second click on it, or a
    click on another folder, changes the folder filter.
    """

    stage_triggered = Signal(str)
    filter_changed = Signal(str)
    folder_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the four sections of the sidebar."""
        super().__init__(parent)
        self._palette: Palette = DARK_PALETTE
        self._counts: dict[WorkflowStage, int] = dict.fromkeys(STAGE_ORDER, 0)
        self._current: WorkflowStage = WorkflowStage.IMPORT
        # The full path of the folder that narrows the table, or "" for every folder.
        self._current_folder = ""
        self.filter_count_labels: dict[LibraryFilter, QLabel] = {}
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(SIDEBAR_MIN_WIDTH)
        self.setAccessibleName(SIDEBAR_NAME)
        content = QWidget(self)
        self._layout = QVBoxLayout(content)
        self.stage_buttons = self._add_stage_section()
        self.filter_buttons, self._filter_group = self._add_filter_section()
        self._add_heading(FOLDERS_HEADING)
        self._folder_box = QVBoxLayout()
        self._layout.addLayout(self._folder_box)
        # One checkable button per folder, or one label when the library has no folder.
        self.folder_labels: list[QLabel | QToolButton] = []
        self._folder_buttons: dict[str, QToolButton] = {}
        self._add_planned_section()
        self._layout.addStretch()
        self.setWidget(content)
        self.set_folders([])
        self._render()

    def _add_heading(self, text: str) -> None:
        """Add a muted caption heading above a section."""
        heading = QLabel(text, self)
        heading.setProperty(TEXT_ROLE_PROPERTY, CAPTION_TEXT_ROLE)
        self._layout.addWidget(heading)

    def _add_stage_section(self) -> dict[WorkflowStage, QToolButton]:
        """Add one button per stage. Each emits its stage when clicked."""
        self._add_heading(WORKFLOW_HEADING)
        buttons: dict[WorkflowStage, QToolButton] = {}
        for stage in STAGE_ORDER:
            button = self._build_row_button()
            button.clicked.connect(lambda _checked=False, chosen=stage: self._emit_stage(chosen))
            buttons[stage] = button
            self._layout.addWidget(button)
        return buttons

    def _add_filter_section(self) -> tuple[dict[LibraryFilter, QToolButton], QButtonGroup]:
        """Add the four filter buttons in one exclusive group, with All tracks checked.

        A caption beside each button shows its track count. The button text stays the
        filter name.
        """
        self._add_heading(LIBRARY_HEADING)
        group = QButtonGroup(self)
        group.setExclusive(True)
        buttons: dict[LibraryFilter, QToolButton] = {}
        for library_filter in LibraryFilter:
            button = self._build_row_button()
            button.setText(FILTER_LABELS[library_filter])
            button.setAccessibleName(FILTER_LABELS[library_filter])
            button.setCheckable(True)
            group.addButton(button)
            button.toggled.connect(
                lambda checked, chosen=library_filter: self._emit_filter(chosen, checked)
            )
            buttons[library_filter] = button
            count_label = QLabel(self)
            count_label.setProperty(TEXT_ROLE_PROPERTY, CAPTION_TEXT_ROLE)
            self.filter_count_labels[library_filter] = count_label
            row = QHBoxLayout()
            row.addWidget(button, 1)
            row.addWidget(count_label)
            self._layout.addLayout(row)
        buttons[LibraryFilter.ALL].setChecked(True)
        return buttons, group

    def set_filter_counts(self, counts: dict[LibraryFilter, int]) -> None:
        """Show the track count of each library filter beside its button."""
        for library_filter, label in self.filter_count_labels.items():
            count = counts.get(library_filter, 0)
            label.setText(str(count))
            button = self.filter_buttons[library_filter]
            button.setAccessibleDescription(format_count(count, "track", "tracks"))

    def _add_planned_section(self) -> None:
        """Add the playlists and crates entries, marked as planned and disabled."""
        self._add_heading(LISTS_HEADING)
        for text in PLANNED_LISTS:
            label = QLabel(text, self)
            label.setEnabled(False)
            self._layout.addWidget(label)

    def _build_row_button(self) -> QToolButton:
        """Return a full-width flat button for one sidebar row."""
        button = QToolButton(self)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(SIDEBAR_ICON_SIZE, SIDEBAR_ICON_SIZE))
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return button

    def _emit_stage(self, stage: WorkflowStage) -> None:
        """Pass a stage click on to the window."""
        self.stage_triggered.emit(stage.value)

    def _emit_filter(self, library_filter: LibraryFilter, checked: bool) -> None:
        """Report a newly checked filter. The unchecked partner of a switch stays silent."""
        if checked:
            self.filter_changed.emit(library_filter.value)

    def current_filter(self) -> LibraryFilter:
        """Return the checked library filter."""
        for library_filter, button in self.filter_buttons.items():
            if button.isChecked():
                return library_filter
        return LibraryFilter.ALL

    def select_filter(self, library_filter: LibraryFilter) -> None:
        """Check a library filter, which emits filter_changed when it changes."""
        self.filter_buttons[library_filter].setChecked(True)

    def set_counts(self, counts: dict[WorkflowStage, int], current: WorkflowStage) -> None:
        """Show new stage counts and mark the current stage. Unchanged counts skip renders."""
        new_counts = {stage: counts.get(stage, 0) for stage in STAGE_ORDER}
        if new_counts == self._counts and current is self._current:
            return
        self._counts = new_counts
        self._current = current
        self._render()

    def set_palette(self, palette: Palette) -> None:
        """Draw the icons in the colors of the theme in effect."""
        self._palette = palette
        self._render()

    def set_folders(self, folders: list[tuple[Path, int]]) -> None:
        """Replace the folder rows with one row per source folder and its track count.

        A chosen folder that no longer holds tracks stops narrowing the table. The
        change emits no signal. The caller already refreshes the table.
        """
        for row in self.folder_labels:
            self._folder_box.removeWidget(row)
            row.deleteLater()
        self.folder_labels = []
        self._folder_buttons = {}
        paths = {str(folder) for folder, _count in folders}
        if self._current_folder not in paths:
            self._current_folder = ""
        if not folders:
            label = QLabel(NO_FOLDERS_TEXT, self)
            label.setTextFormat(Qt.TextFormat.PlainText)
            self._folder_box.addWidget(label)
            self.folder_labels.append(label)
            return
        for folder, count in folders:
            self._add_folder_button(format_folder_row(folder, count), str(folder))

    def _add_folder_button(self, text: str, full_path: str) -> None:
        """Add one checkable folder row that toggles the folder filter."""
        button = self._build_row_button()
        # A folder name can hold an ampersand. Doubling it shows it instead of a shortcut.
        button.setText(text.replace("&", "&&"))
        button.setAccessibleName(text)
        button.setToolTip(full_path)
        button.setCheckable(True)
        button.setChecked(full_path == self._current_folder)
        button.clicked.connect(lambda _checked=False, path=full_path: self._toggle_folder(path))
        self._folder_box.addWidget(button)
        self.folder_labels.append(button)
        self._folder_buttons[full_path] = button

    def _toggle_folder(self, full_path: str) -> None:
        """Narrow the table to a folder, or show every folder when it is already chosen."""
        self.select_folder("" if full_path == self._current_folder else full_path)

    def current_folder(self) -> str:
        """Return the full path of the chosen folder, or "" when every folder shows."""
        return self._current_folder

    def select_folder(self, full_path: str) -> None:
        """Choose a folder by full path, or "" for every folder. A change emits folder_changed."""
        for path, button in self._folder_buttons.items():
            button.setChecked(path == full_path)
        if full_path == self._current_folder:
            return
        self._current_folder = full_path
        self.folder_changed.emit(full_path)

    def _render(self) -> None:
        """Apply the counts, current mark, and icons to the stage buttons."""
        for stage, button in self.stage_buttons.items():
            is_current = stage is self._current
            count = self._counts[stage]
            button.setText(f"{STAGE_LABELS[stage]}  {count}")
            button.setAccessibleName(f"{STAGE_LABELS[stage]}, {count}")
            color = self._palette.highlight if is_current else self._palette.text_muted
            button.setIcon(themed_icon(STAGE_ICONS[stage], color))
            button.setProperty(CURRENT_STAGE_PROPERTY, is_current)
            button.style().unpolish(button)
            button.style().polish(button)
