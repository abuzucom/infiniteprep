"""Show the search feedback of the library window.

The module holds the result summary, the filter chips, the empty state, and the
search field keys. The window owns the search, filter, and sort state. The
widgets here show that state and report the clicks that change it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton, QWidget

from infiniteprep.domain.library_query import plain_search_term, query_fields
from infiniteprep.ui.icon_loader import themed_icon
from infiniteprep.ui.tokens import Palette
from infiniteprep.ui.workflow import LibraryFilter, matches_library_filter
from infiniteprep.ui.write_preview_model import format_count

# Milliseconds after the last typed key before the search runs. Programmatic text runs at once.
SEARCH_DEBOUNCE_MS: Final[int] = 250
EMPTY_LIBRARY_TEXT: Final[str] = "No tracks yet. Add a music folder to get started."
NO_MATCHES_TEXT: Final[str] = (
    "No tracks match your search and filters. Try other words, or clear them."
)
ADD_FOLDER_TEXT: Final[str] = "Add folder"
CLEAR_SEARCH_TEXT: Final[str] = "Clear search and filters"
CLEAR_ALL_TEXT: Final[str] = "Clear all"
STALE_RESULTS_TEXT: Final[str] = "Showing your last results. Fix the search to update them."
SUMMARY_NAME: Final[str] = "Search results"
EMPTY_STATE_NAME: Final[str] = "No tracks to show"
REMOVE_FILTER_TOOLTIP: Final[str] = "Remove this filter"
REMOVE_CHIP_NAME_TEMPLATE: Final[str] = "Remove filter {label}"
FOLDER_CHIP_TEMPLATE: Final[str] = "Folder: {name}"
CHIP_ICON: Final[str] = "x"
RENAME_SEARCH_MENU_TEXT: Final[str] = "Rename"
DELETE_SEARCH_MENU_TEXT: Final[str] = "Delete"
RENAME_SEARCH_TITLE: Final[str] = "Rename saved search"
RENAME_SEARCH_LABEL: Final[str] = "New name"
DELETE_SEARCH_TITLE: Final[str] = "Delete saved search"
DELETE_SEARCH_TEMPLATE: Final[str] = (
    'Delete the saved search "{name}"? Your tracks stay in the library.'
)
HIDDEN_MATCH_TEMPLATE: Final[str] = "Matched {field}: {value}"
# Searchable fields without a table column, mapped to the record field each one reads.
# The keyword field searches the shown text and the genre. Only the genre lacks a column.
HIDDEN_MATCH_FIELDS: Final[dict[str, str]] = {
    "keyword": "genre",
    "genre": "genre",
    "year": "year",
}


@dataclass(frozen=True)
class MatchHints:
    """How the table marks a search: the plain term to bold and a tooltip per file ID."""

    term: str = ""
    tooltips: Mapping[str, str] = field(default_factory=dict)


def format_result_summary(shown: int, total: int) -> str:
    """Return the result count, such as "Showing 12 of 340 tracks" or "340 tracks"."""
    total_text = format_count(total, "track", "tracks")
    if shown == total:
        return total_text
    return f"Showing {shown} of {total_text}"


def format_hidden_match(record: Mapping[str, object], fields: Iterable[str]) -> str:
    """Return one line per searched field without a column that holds a value, or ""."""
    record_fields = sorted(
        {HIDDEN_MATCH_FIELDS[name] for name in fields if name in HIDDEN_MATCH_FIELDS}
    )
    lines = [
        HIDDEN_MATCH_TEMPLATE.format(field=name, value=record[name])
        for name in record_fields
        if record.get(name) not in (None, "")
    ]
    return "\n".join(lines)


def build_match_hints(search_text: str, records: Iterable[Mapping[str, object]]) -> MatchHints:
    """Return the marks for a search over the given search records."""
    if not search_text.strip():
        return MatchHints()
    term = plain_search_term(search_text) or ""
    fields = query_fields(search_text)
    if not fields & HIDDEN_MATCH_FIELDS.keys():
        return MatchHints(term)
    tooltips = {
        str(record["id"]): tooltip
        for record in records
        if (tooltip := format_hidden_match(record, fields))
    }
    return MatchHints(term, tooltips)


def count_library_filters(tracks: Iterable[tuple[str, bool]]) -> dict[LibraryFilter, int]:
    """Return the track count of each library filter from (status, has warnings) pairs."""
    counts = dict.fromkeys(LibraryFilter, 0)
    for status, has_warnings in tracks:
        for library_filter in LibraryFilter:
            counts[library_filter] += int(
                matches_library_filter(library_filter, status, has_warnings)
            )
    return counts


def escape_mnemonic(text: str) -> str:
    """Return text for a button label with each ampersand shown, not read as a shortcut."""
    return text.replace("&", "&&")


class SearchFieldKeys(QObject):
    """Empty a search field on Escape. An empty field passes Escape on to its window."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Clear the watched field on Escape and report the key as handled."""
        if event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return False
        if event.key() != Qt.Key.Key_Escape or not isinstance(watched, QLineEdit):
            return False
        if not watched.text():
            return False
        # setText marks the text as unmodified. The window then searches at once.
        watched.setText("")
        return True


class EmptyStateBanner(QWidget):
    """Say why the table is empty and offer the next step."""

    add_folder_requested = Signal()
    clear_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the message and the two actions. The banner starts hidden."""
        super().__init__(parent)
        self.setAccessibleName(EMPTY_STATE_NAME)
        layout = QHBoxLayout(self)
        self.message_label = QLabel(self)
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        self.message_label.setWordWrap(True)
        self.add_folder_btn = QPushButton(ADD_FOLDER_TEXT, self)
        self.clear_btn = QPushButton(CLEAR_SEARCH_TEXT, self)
        self.add_folder_btn.clicked.connect(self.add_folder_requested)
        self.clear_btn.clicked.connect(self.clear_requested)
        layout.addWidget(self.message_label, 1)
        layout.addWidget(self.add_folder_btn)
        layout.addWidget(self.clear_btn)
        self.hide()

    def show_state(self, library_empty: bool) -> None:
        """Show the empty library state, or the no matches state."""
        self.message_label.setText(EMPTY_LIBRARY_TEXT if library_empty else NO_MATCHES_TEXT)
        self.add_folder_btn.setVisible(library_empty)
        self.clear_btn.setVisible(not library_empty)
        self.show()


class ActiveFilterBar(QWidget):
    """Show the result count, a removable chip per active filter, and Clear all."""

    status_removed = Signal()
    folder_removed = Signal()
    clear_all_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the summary, the two chips, and the Clear all button."""
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary_label = QLabel(self)
        self.summary_label.setTextFormat(Qt.TextFormat.PlainText)
        self.summary_label.setAccessibleName(SUMMARY_NAME)
        self.status_chip = self._build_chip()
        self.folder_chip = self._build_chip()
        self.clear_all_btn = QToolButton(self)
        self.clear_all_btn.setText(CLEAR_ALL_TEXT)
        self.clear_all_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clear_all_btn.hide()
        self.status_chip.clicked.connect(self.status_removed)
        self.folder_chip.clicked.connect(self.folder_removed)
        self.clear_all_btn.clicked.connect(self.clear_all_requested)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.status_chip)
        layout.addWidget(self.folder_chip)
        layout.addStretch()
        layout.addWidget(self.clear_all_btn)

    def _build_chip(self) -> QToolButton:
        """Return a hidden chip button that removes one filter when clicked."""
        chip = QToolButton(self)
        chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        chip.setToolTip(REMOVE_FILTER_TOOLTIP)
        chip.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        chip.hide()
        return chip

    def set_palette(self, palette: Palette) -> None:
        """Draw the chip icons in the text color of the theme in effect."""
        icon = themed_icon(CHIP_ICON, palette.text)
        self.status_chip.setIcon(icon)
        self.folder_chip.setIcon(icon)

    def show_state(self, summary: str, status_label: str, folder_name: str) -> None:
        """Show the summary and a chip for each active filter. Empty labels hide chips."""
        self.summary_label.setText(summary)
        folder_label = FOLDER_CHIP_TEMPLATE.format(name=folder_name) if folder_name else ""
        self._show_chip(self.status_chip, status_label)
        self._show_chip(self.folder_chip, folder_label)

    def show_clear_all(self, shown: bool) -> None:
        """Show Clear all while a search or a filter narrows the table."""
        self.clear_all_btn.setVisible(shown)

    def show_stale(self) -> None:
        """Say the table still shows the results of the last valid search."""
        self.summary_label.setText(STALE_RESULTS_TEXT)

    @staticmethod
    def _show_chip(chip: QToolButton, label: str) -> None:
        """Show one chip with its label, or hide it when the label is empty."""
        chip.setText(escape_mnemonic(label))
        chip.setAccessibleName(REMOVE_CHIP_NAME_TEMPLATE.format(label=label))
        chip.setVisible(bool(label))
