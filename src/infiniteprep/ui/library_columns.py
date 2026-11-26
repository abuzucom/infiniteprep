"""Library table columns: hide rules, the header menu, and the status and key cells.

Title, Artist, and Filename stretch. Every other column fits its cells. Label / Cat#
and Filename show once the table reaches 1100 px. Format, Duration, and Mix hide
below a 1000 px table. A choice made in the header menu wins over these rules and
persists.
"""

from __future__ import annotations

import json
import logging
from enum import IntEnum
from typing import Final

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtWidgets import QMenu, QTableWidget

from infiniteprep.domain.enums import (
    KeyDisplayNotation,
    KeyMode,
    PitchClass,
    ReviewStatus,
    format_canonical_key,
)
from infiniteprep.ui import library_rows
from infiniteprep.ui.tokens import KeyCellColors, Palette, StatusRole, key_cell_colors

logger = logging.getLogger(__name__)


class LibraryColumn(IntEnum):
    """The library table columns, by their logical index."""

    FILENAME = 0
    FORMAT = 1
    DURATION = 2
    ARTIST = 3
    TITLE = 4
    MIX = 5
    LABEL = 6
    BPM = 7
    KEY = 8
    STATUS = 9
    WARNINGS = 10


NEVER_HIDDEN: Final[frozenset[LibraryColumn]] = frozenset(
    {
        LibraryColumn.ARTIST,
        LibraryColumn.TITLE,
        LibraryColumn.BPM,
        LibraryColumn.KEY,
        LibraryColumn.STATUS,
        LibraryColumn.WARNINGS,
    }
)
WIDE_TABLE_COLUMNS: Final[frozenset[LibraryColumn]] = frozenset(
    {LibraryColumn.LABEL, LibraryColumn.FILENAME}
)
NARROW_HIDDEN_COLUMNS: Final[frozenset[LibraryColumn]] = frozenset(
    {LibraryColumn.FORMAT, LibraryColumn.DURATION, LibraryColumn.MIX}
)
WIDE_TABLE_MIN_WIDTH: Final[int] = 1100
NARROW_TABLE_MAX_WIDTH: Final[int] = 1000
RESET_COLUMNS_LABEL: Final[str] = "Reset columns"


def hides_for_width(column: LibraryColumn, table_width: int) -> bool:
    """Return True when the width rules hide a column at a table width."""
    if column in WIDE_TABLE_COLUMNS:
        return table_width < WIDE_TABLE_MIN_WIDTH
    if column in NARROW_HIDDEN_COLUMNS:
        return table_width < NARROW_TABLE_MAX_WIDTH
    return False


def is_column_hidden(column: LibraryColumn, table_width: int, choices: dict[int, bool]) -> bool:
    """Return True when a column hides: never for the core six, else the user's choice wins."""
    if column in NEVER_HIDDEN:
        return False
    shown = choices.get(int(column))
    if shown is not None:
        return not shown
    return hides_for_width(column, table_width)


def parse_column_choices(text: str) -> dict[int, bool]:
    """Read stored header menu choices. Invalid entries drop, so a bad file loads empty."""
    if not text:
        return {}
    try:
        raw = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("Ignoring stored column choices that are not valid JSON.")
        return {}
    if not isinstance(raw, dict):
        return {}
    valid_columns = {str(int(column)) for column in LibraryColumn}
    return {
        int(key): value
        for key, value in raw.items()
        if key in valid_columns and isinstance(value, bool)
    }


def format_column_choices(choices: dict[int, bool]) -> str:
    """Return the stored form of the header menu choices."""
    if not choices:
        return ""
    return json.dumps({str(column): shown for column, shown in sorted(choices.items())})


class ColumnVisibility(QObject):
    """Hide library columns by table width and by the choices made in the header menu.

    The header right-click menu lists every column. The six core columns stay checked
    and disabled. Reset columns clears every choice, so the width rules apply again.
    """

    changed = Signal()

    def __init__(
        self, table: QTableWidget, labels: tuple[str, ...], choices: dict[int, bool]
    ) -> None:
        """Watch the table width and offer the header menu."""
        super().__init__(table)
        self._table = table
        self._labels = labels
        self._choices = dict(choices)
        self._table_alive = True
        header = table.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._open_menu)
        table.destroyed.connect(self._on_table_destroyed)
        table.installEventFilter(self)

    @property
    def choices(self) -> dict[int, bool]:
        """Return a copy of the header menu choices."""
        return dict(self._choices)

    def apply(self) -> None:
        """Show and hide each column for the current table width and choices."""
        header = self._table.horizontalHeader()
        width = self._table.width()
        for column in LibraryColumn:
            hidden = is_column_hidden(column, width, self._choices)
            if header.isSectionHidden(column) != hidden:
                header.setSectionHidden(column, hidden)

    def set_choice(self, column: LibraryColumn, shown: bool) -> None:
        """Record a header menu choice, apply it, and report the change."""
        if column in NEVER_HIDDEN:
            return
        self._choices[int(column)] = shown
        self.apply()
        self.changed.emit()

    def reset_choices(self) -> None:
        """Drop every header menu choice, so the width rules decide again."""
        self._choices.clear()
        self.apply()
        self.changed.emit()

    def build_menu(self) -> QMenu:
        """Return the header menu: one checkable entry per column, then Reset columns."""
        menu = QMenu(self._table)
        header = self._table.horizontalHeader()
        for column in LibraryColumn:
            action = menu.addAction(self._labels[column])
            action.setCheckable(True)
            action.setChecked(not header.isSectionHidden(column))
            action.setEnabled(column not in NEVER_HIDDEN)
            action.toggled.connect(lambda shown, chosen=column: self.set_choice(chosen, shown))
        menu.addSeparator()
        menu.addAction(RESET_COLUMNS_LABEL).triggered.connect(self.reset_choices)
        return menu

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Apply the width rules again whenever the table changes size."""
        is_resize = event.type() == QEvent.Type.Resize and watched is self._table
        if is_resize and self._table_alive:
            self.apply()
        return False

    def _open_menu(self, position: QPoint) -> None:
        """Show the header menu at the pointer."""
        menu = self.build_menu()
        menu.exec(self._table.horizontalHeader().mapToGlobal(position))

    def _on_table_destroyed(self) -> None:
        """Stop applying rules once Qt starts deleting the table."""
        self._table_alive = False


# Each review status shows one state icon in one status color.
STATUS_STATES: Final[dict[ReviewStatus, tuple[str, StatusRole]]] = {
    ReviewStatus.APPLIED: ("circle-check", StatusRole.DONE),
    ReviewStatus.WRITTEN: ("circle-check", StatusRole.DONE),
    ReviewStatus.APPROVED: ("circle-dashed-check", StatusRole.READY),
    ReviewStatus.READY_TO_WRITE: ("circle-dashed-check", StatusRole.READY),
    ReviewStatus.PROCESSING: ("loader", StatusRole.READY),
    ReviewStatus.UNPROCESSED: ("circle-dashed", StatusRole.WAITING),
    ReviewStatus.PROPOSED: ("triangle-exclamation-point", StatusRole.WARNING),
    ReviewStatus.READY_FOR_REVIEW: ("triangle-exclamation-point", StatusRole.WARNING),
    ReviewStatus.CONFLICTING_CANDIDATES: ("triangle-exclamation-point", StatusRole.WARNING),
    ReviewStatus.PARTIALLY_IDENTIFIED: ("triangle-exclamation-point", StatusRole.WARNING),
    ReviewStatus.PARTIALLY_APPROVED: ("triangle-exclamation-point", StatusRole.WARNING),
    ReviewStatus.NO_MATCH: ("triangle-exclamation-point", StatusRole.WARNING),
    ReviewStatus.ERROR: ("circle-x", StatusRole.FAILED),
}
WARNING_ICON: Final[str] = "triangle-exclamation-point"
MINOR_MODE_SUFFIX: Final[str] = "m"
# Retain the column formatting interface used by library views.
STATUS_LABELS = library_rows.STATUS_LABELS
format_status_cell = library_rows.format_status_cell
format_status_label = library_rows.format_status_label


def status_icon(status: str, palette: Palette) -> tuple[str, str] | None:
    """Return the icon name and color of a stored status, or None for an unknown value."""
    try:
        state = STATUS_STATES[ReviewStatus(status)]
    except ValueError:
        return None
    icon_name, role = state
    return icon_name, palette.status[role]


def key_cell_style(key: tuple[PitchClass, KeyMode] | None) -> KeyCellColors | None:
    """Return the key cell colors for a parsed key, or None when the key is unknown.

    The Open Key form of the key gives the wheel number and the mode: 4m is minor.
    """
    if key is None:
        return None
    open_key = format_canonical_key(key[0], key[1], KeyDisplayNotation.OPEN_KEY)
    number_text = open_key[:-1]
    if not number_text.isdigit():
        return None
    return key_cell_colors(int(number_text), open_key.endswith(MINOR_MODE_SUFFIX))
