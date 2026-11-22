"""The Settings section list: icons and titles on the left, the chosen section on the right."""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

SECTION_LIST_WIDTH: Final[int] = 180
SECTION_ICON_SIZE: Final[int] = 20
SECTION_LIST_NAME: Final[str] = "Settings sections"
SECTION_TITLE_NAME: Final[str] = "settings_section_title"
SECTION_NOTE_NAME: Final[str] = "settings_section_note"


class SettingsSectionView(QWidget):
    """Show one Settings section at a time, picked from a list with icons.

    Each section shows its title and a note on when its changes take effect above its
    page. The view keeps the tab methods the dialog used before: addTab, count,
    tabText, widget, currentWidget, and setCurrentWidget.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the empty list and the empty page stack."""
        super().__init__(parent)
        self.section_list = QListWidget(self)
        self.section_list.setFixedWidth(SECTION_LIST_WIDTH)
        self.section_list.setIconSize(QSize(SECTION_ICON_SIZE, SECTION_ICON_SIZE))
        self.section_list.setAccessibleName(SECTION_LIST_NAME)
        self.pages = QStackedWidget(self)
        self._titles: list[str] = []
        self.notes: list[str] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.section_list)
        layout.addWidget(self.pages, stretch=1)
        self.section_list.currentRowChanged.connect(self.pages.setCurrentIndex)

    def addTab(self, page: QWidget, title: str, icon: QIcon | None = None, note: str = "") -> int:
        """Add a section with its icon, title, and timing note. Return its index."""
        holder = QWidget(self.pages)
        holder_layout = QVBoxLayout(holder)
        heading = QLabel(title, holder)
        heading.setObjectName(SECTION_TITLE_NAME)
        holder_layout.addWidget(heading)
        if note:
            note_label = QLabel(note, holder)
            note_label.setObjectName(SECTION_NOTE_NAME)
            note_label.setWordWrap(True)
            holder_layout.addWidget(note_label)
        holder_layout.addWidget(page, stretch=1)
        self.pages.addWidget(holder)
        item = QListWidgetItem(icon or QIcon(), title)
        item.setData(Qt.ItemDataRole.UserRole, page)
        self.section_list.addItem(item)
        self._titles.append(title)
        self.notes.append(note)
        if self.section_list.currentRow() < 0:
            self.section_list.setCurrentRow(0)
        return len(self._titles) - 1

    def count(self) -> int:
        """Return the number of sections."""
        return len(self._titles)

    def tabText(self, index: int) -> str:
        """Return the title of a section."""
        return self._titles[index]

    def widget(self, index: int) -> QWidget:
        """Return the settings page of a section."""
        return self.section_list.item(index).data(Qt.ItemDataRole.UserRole)

    def currentIndex(self) -> int:
        """Return the index of the shown section."""
        return self.section_list.currentRow()

    def setCurrentIndex(self, index: int) -> None:
        """Show a section by its index."""
        self.section_list.setCurrentRow(index)

    def currentWidget(self) -> QWidget:
        """Return the settings page of the shown section."""
        return self.widget(self.currentIndex())

    def setCurrentWidget(self, page: QWidget) -> None:
        """Show the section whose settings page is page."""
        for index in range(self.count()):
            if self.widget(index) is page:
                self.setCurrentIndex(index)
                return
