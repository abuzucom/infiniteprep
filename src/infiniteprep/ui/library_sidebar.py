"""The sidebar: workflow stages, library filters, source folders, and planned lists."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
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
    """

    stage_triggered = Signal(str)
    filter_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the four sections of the sidebar."""
        super().__init__(parent)
        self._palette: Palette = DARK_PALETTE
        self._counts: dict[WorkflowStage, int] = dict.fromkeys(STAGE_ORDER, 0)
        self._current: WorkflowStage = WorkflowStage.IMPORT
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
        self.folder_labels: list[QLabel] = []
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
        """Add the four filter buttons in one exclusive group, with All tracks checked."""
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
            self._layout.addWidget(button)
        buttons[LibraryFilter.ALL].setChecked(True)
        return buttons, group

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
        """Replace the folder rows with one row per source folder and its track count."""
        for label in self.folder_labels:
            self._folder_box.removeWidget(label)
            label.deleteLater()
        rows = [(format_folder_row(folder, count), str(folder)) for folder, count in folders]
        if not rows:
            rows = [(NO_FOLDERS_TEXT, "")]
        self.folder_labels = []
        for text, full_path in rows:
            label = QLabel(text, self)
            label.setToolTip(full_path)
            label.setTextFormat(Qt.TextFormat.PlainText)
            self._folder_box.addWidget(label)
            self.folder_labels.append(label)

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
