"""Share a table's spare width among weighted columns and fit the other columns."""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QHeaderView, QTableWidget


class WeightedColumnSizer(QObject):
    """Size a table's columns: weighted columns share the spare width, the rest fit content.

    A column the user resizes keeps that width. A restored header layout counts as
    user widths, so a saved layout stays as saved. No column uses the last-section
    stretch, which handed all spare width to whatever column came last.
    """

    def __init__(self, table: QTableWidget, weights: Mapping[int, int], minimum_width: int) -> None:
        """Size the table's columns now and again on each viewport resize.

        Raises ValueError for a weight below one or a column outside the table, since
        either one leaves the spare width undistributed.
        """
        super().__init__(table)
        column_count = table.columnCount()
        for column, weight in weights.items():
            if not 0 <= column < column_count:
                raise ValueError(f"Column {column} is outside the table. Pass a column index.")
            if weight < 1:
                raise ValueError(
                    f"Column {column} has weight {weight}. Pass a weight of 1 or more."
                )
        if minimum_width < 1:
            raise ValueError("The minimum column width must be at least 1 pixel.")
        self._table = table
        self._weights = dict(weights)
        self._minimum_width = minimum_width
        self._pinned: set[int] = set()
        self._applying = False
        # Qt still delivers viewport events while it deletes the table. Any call on the
        # deleted table then raises, so the filter keeps its own viewport reference and
        # stops once the table is gone.
        self._table_alive = True
        self._viewport = table.viewport()
        self.reset_header_modes()
        table.horizontalHeader().sectionResized.connect(self._on_section_resized)
        table.destroyed.connect(self._on_table_destroyed)
        self._viewport.installEventFilter(self)

    def reset_header_modes(self) -> None:
        """Make every column user-resizable and turn the last-section stretch off.

        A header state from an older version carries the stretch, so a restore calls
        this again.
        """
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)

    def keep_restored_widths(self) -> None:
        """Treat every column width as set by the user after a saved layout loads."""
        self.reset_header_modes()
        self._pinned = set(range(self._table.columnCount()))

    def is_pinned(self, column: int) -> bool:
        """Return True when the user set this column's width."""
        return column in self._pinned

    def refit(self) -> None:
        """Fit the unweighted columns to their cells, then share the spare width."""
        for column in range(self._table.columnCount()):
            if column in self._weights or column in self._pinned:
                continue
            self._resize(column, self._content_width(column))
        self.share_spare_width()

    def share_spare_width(self) -> None:
        """Split the width the other columns leave among the weighted columns."""
        header = self._table.horizontalHeader()
        free = [
            column
            for column in self._weights
            if column not in self._pinned and not header.isSectionHidden(column)
        ]
        if not free:
            return
        used = sum(
            header.sectionSize(column)
            for column in range(header.count())
            if column not in free and not header.isSectionHidden(column)
        )
        spare = max(0, self._table.viewport().width() - used)
        # Every weight is at least one, so the total of a non-empty list is positive.
        total_weight = sum(self._weights[column] for column in free)
        for column in free:
            share = spare * self._weights[column] // total_weight
            self._resize(column, max(self._minimum_width, share))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Share the spare width again whenever the table viewport changes size."""
        is_viewport_resize = event.type() == QEvent.Type.Resize and watched is self._viewport
        if is_viewport_resize and self._table_alive:
            self.share_spare_width()
        return False

    def _on_table_destroyed(self) -> None:
        """Stop sizing once Qt starts deleting the table."""
        self._table_alive = False

    def _content_width(self, column: int) -> int:
        """Return the width the column's cells need, or the header's width for no rows.

        The header text does not widen a column whose cells are narrow. The header
        elides instead and shows its full text in a tooltip.
        """
        content = self._table.sizeHintForColumn(column)
        if content > 0:
            return content
        return self._table.horizontalHeader().sectionSizeHint(column)

    def _resize(self, column: int, width: int) -> None:
        """Resize one column without counting it as a user resize."""
        self._applying = True
        try:
            self._table.horizontalHeader().resizeSection(column, width)
        finally:
            self._applying = False

    def _on_section_resized(self, column: int, _old_width: int, _new_width: int) -> None:
        """Keep a width the user set. Sizer resizes do not count."""
        if not self._applying:
            self._pinned.add(column)
