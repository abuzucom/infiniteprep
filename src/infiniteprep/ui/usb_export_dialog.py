"""The USB export dialog in two steps: Choose drive, then Confirm.

The Choose drive step lists drives on a USB, SD, or MMC bus only. The Confirm step
names the drive, shows the space it needs, and lists what happens on the drive.
Drive text is untrusted and renders as plain text. Raw command errors never reach
the dialog.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.services.usb.models import DriveInspection, DriveInspectionError
from infiniteprep.services.usb.preflight import (
    SAFETY_REFUSAL_CODES,
    PreflightRequest,
    PreflightResult,
    evaluate_preflight,
)
from infiniteprep.services.usb.targets import TargetCandidate
from infiniteprep.ui.collapsible_section import CollapsibleSection
from infiniteprep.ui.usb_drive_picker_dialog import describe_candidate
from infiniteprep.ui.usb_export_model import (
    SPACE_BAR_STEPS,
    detail_rows,
    drive_change_lines,
    drive_title,
    needed_bytes,
    space_bar_value,
    space_summary,
)

logger = logging.getLogger(__name__)

USB_EXPORT_DIALOG_WIDTH: Final[int] = 640
USB_EXPORT_DIALOG_HEIGHT: Final[int] = 560
PAGE_CHOOSE: Final[int] = 0
PAGE_CONFIRM: Final[int] = 1
WINDOW_TITLE: Final[str] = "Export to USB"
STEP_TITLES: Final[tuple[str, str]] = ("1  Choose drive", "2  Confirm")
CHOOSE_PROMPT_TEXT: Final[str] = "Pick the drive to export to."
NO_DRIVES_TEXT: Final[str] = "No USB drives or memory cards found. Connect one and scan again."
LOAD_FAILED_TEXT: Final[str] = "Couldn't list your drives. Reconnect the drive and scan again."
DRIVE_TITLE: Final[str] = "Drive"
DETAILS_TITLE: Final[str] = "Drive details"
SPACE_TITLE: Final[str] = "Space"
CHANGES_TITLE: Final[str] = "What happens on the drive"
REFUSALS_TITLE: Final[str] = "You can't export to this drive"
NOTES_TITLE: Final[str] = "Good to know"
PLAYERS_PREFIX: Final[str] = "For "
LIST_SEPARATOR: Final[str] = ", "
DETAIL_ROW_SEPARATOR: Final[str] = ": "
DETAIL_LINE_SEPARATOR: Final[str] = "\n"

TargetLoader = Callable[[], tuple[TargetCandidate, ...]]
PreflightEvaluator = Callable[[DriveInspection, PreflightRequest], PreflightResult]


def plain_label(parent: QWidget) -> QLabel:
    """Return a label that shows markup in drive text as plain characters."""
    label = QLabel(parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def titled_list(title: str, parent: QWidget) -> tuple[QWidget, QListWidget]:
    """Return a section with a heading above a list, and the list."""
    section = QWidget(parent)
    layout = QVBoxLayout(section)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(QLabel(title, section))
    items = QListWidget(section)
    layout.addWidget(items)
    return section, items


class UsbExportDialog(QDialog):
    """Pick a USB drive, then confirm the export to it.

    Export turns on only when the preflight passes. Enter cancels, since the
    export changes the drive. A safety refusal hides the drive and its space.
    """

    def __init__(
        self,
        loader: TargetLoader,
        request: PreflightRequest,
        parent: QWidget | None = None,
        *,
        evaluate: PreflightEvaluator = evaluate_preflight,
    ) -> None:
        """Build both steps and load the drive list once.

        loader lists the drives, such as load_removable_targets. request describes
        the planned export that the Confirm step checks against each drive.
        """
        super().__init__(parent)
        self._loader = loader
        self._request = request
        self._evaluate = evaluate
        self._candidates: tuple[TargetCandidate, ...] = ()
        self._confirmed: DriveInspection | None = None
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(USB_EXPORT_DIALOG_WIDTH, USB_EXPORT_DIALOG_HEIGHT)
        self.step_labels = [QLabel(title, self) for title in STEP_TITLES]
        self.pages = QStackedWidget(self)
        self.pages.addWidget(self._build_choose_page())
        self.pages.addWidget(self._build_confirm_page())
        self._build_layout()
        self._show_page(PAGE_CHOOSE)
        self.reload()

    def _build_choose_page(self) -> QWidget:
        """Build the drive list, its status line, and the scan button."""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.drive_list = QListWidget(page)
        self.status_label = plain_label(page)
        self.rescan_btn = QPushButton("Scan again", page)
        layout.addWidget(self.drive_list, stretch=1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.rescan_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self.drive_list.currentRowChanged.connect(self._update_next_state)
        self.rescan_btn.clicked.connect(self.reload)
        return page

    def _build_confirm_page(self) -> QWidget:
        """Build the drive, space, change, refusal, and note sections."""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(self._build_drive_group(page))
        layout.addWidget(self._build_space_group(page))
        changes_section, self.changes_list = titled_list(CHANGES_TITLE, page)
        self.refusals_section, self.refusals_list = titled_list(REFUSALS_TITLE, page)
        self.notes_section, self.notes_list = titled_list(NOTES_TITLE, page)
        for section in (changes_section, self.refusals_section, self.notes_section):
            layout.addWidget(section)
        return page

    def _build_drive_group(self, page: QWidget) -> QGroupBox:
        """Build the drive name, the players line, and the closed details toggle."""
        self.drive_group = QGroupBox(DRIVE_TITLE, page)
        layout = QVBoxLayout(self.drive_group)
        self.drive_name_label = plain_label(self.drive_group)
        self.players_label = plain_label(self.drive_group)
        self.details_label = plain_label(self.drive_group)
        self.details_section = CollapsibleSection(
            DETAILS_TITLE, self.details_label, self.drive_group, expanded=False
        )
        layout.addWidget(self.drive_name_label)
        layout.addWidget(self.players_label)
        layout.addWidget(self.details_section)
        return self.drive_group

    def _build_space_group(self, page: QWidget) -> QGroupBox:
        """Build the space bar and the needed-of-free line."""
        self.space_group = QGroupBox(SPACE_TITLE, page)
        layout = QVBoxLayout(self.space_group)
        self.space_bar = QProgressBar(self.space_group)
        self.space_bar.setRange(0, SPACE_BAR_STEPS)
        self.space_bar.setTextVisible(False)
        self.space_label = plain_label(self.space_group)
        layout.addWidget(self.space_bar)
        layout.addWidget(self.space_label)
        return self.space_group

    def _build_layout(self) -> None:
        """Place the step row, the pages, and the buttons."""
        root = QVBoxLayout(self)
        steps = QHBoxLayout()
        for label in self.step_labels:
            steps.addWidget(label)
        steps.addStretch()
        root.addLayout(steps)
        root.addWidget(self.pages, stretch=1)
        self.back_btn = QPushButton("Back", self)
        self.next_btn = QPushButton("Next", self)
        self.export_btn = QPushButton("Export", self)
        self.cancel_btn = QPushButton("Cancel", self)
        buttons = QHBoxLayout()
        buttons.addWidget(self.back_btn)
        buttons.addStretch()
        for button in (self.next_btn, self.export_btn, self.cancel_btn):
            buttons.addWidget(button)
            button.setAutoDefault(False)
        root.addLayout(buttons)
        self.cancel_btn.setDefault(True)
        self.back_btn.clicked.connect(lambda: self._show_page(PAGE_CHOOSE))
        self.next_btn.clicked.connect(self._confirm_chosen)
        self.export_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

    def _show_page(self, index: int) -> None:
        """Show one step, mark it in the step row, and show its buttons."""
        self.pages.setCurrentIndex(index)
        for position, label in enumerate(self.step_labels):
            font = label.font()
            font.setBold(position == index)
            label.setFont(font)
        on_confirm = index == PAGE_CONFIRM
        self.back_btn.setVisible(on_confirm)
        self.export_btn.setVisible(on_confirm)
        self.next_btn.setVisible(not on_confirm)

    def reload(self) -> None:
        """Load the drive list again and drop the current choice."""
        self.drive_list.clear()
        self._candidates = ()
        try:
            candidates = self._loader()
        except DriveInspectionError as exc:
            # The raw message holds untrusted command output. Log the type only.
            logger.warning("USB drive listing failed: %s", type(exc).__name__)
            self.status_label.setText(LOAD_FAILED_TEXT)
            self._update_next_state()
            return
        self._candidates = candidates
        for candidate in candidates:
            self.drive_list.addItem(_build_drive_item(candidate))
        self.status_label.setText(CHOOSE_PROMPT_TEXT if candidates else NO_DRIVES_TEXT)
        self._update_next_state()

    def chosen_inspection(self) -> DriveInspection | None:
        """Return the inspection of the picked row, or None without a valid pick."""
        row = self.drive_list.currentRow()
        if row < 0 or row >= len(self._candidates):
            return None
        return self._candidates[row].inspection

    def selected_inspection(self) -> DriveInspection | None:
        """Return the drive shown on the Confirm step, or None before it."""
        return self._confirmed

    def _update_next_state(self) -> None:
        """Turn Next on only for a row that holds an inspection."""
        self.next_btn.setEnabled(self.chosen_inspection() is not None)

    def _confirm_chosen(self) -> None:
        """Move to the Confirm step for the picked drive."""
        inspection = self.chosen_inspection()
        if inspection is not None:
            self.show_confirm(inspection)

    def show_confirm(self, inspection: DriveInspection) -> None:
        """Evaluate the export to one drive and show the Confirm step."""
        result = self._evaluate(inspection, self._request)
        self._confirmed = inspection
        self._fill_drive(inspection, result)
        self._fill_space(result)
        _fill_list(self.changes_list, drive_change_lines(self._request))
        refusals = [f"{item.message} {item.guidance}".strip() for item in result.refusals]
        _fill_list(self.refusals_list, refusals)
        self.refusals_section.setVisible(bool(refusals))
        _fill_list(self.notes_list, [note.message for note in result.notes])
        self.notes_section.setVisible(bool(result.notes))
        self.export_btn.setEnabled(result.passed)
        safety = any(item.code in SAFETY_REFUSAL_CODES for item in result.refusals)
        self.drive_group.setVisible(not safety)
        self.space_group.setVisible(not safety)
        self._show_page(PAGE_CONFIRM)

    def _fill_drive(self, inspection: DriveInspection, result: PreflightResult) -> None:
        """Show the drive name, the players, and the identifier rows."""
        self.drive_name_label.setText(drive_title(inspection))
        players = [profile.name for profile in self._request.profiles]
        self.players_label.setText(PLAYERS_PREFIX + LIST_SEPARATOR.join(players))
        self.players_label.setVisible(bool(players))
        rows = detail_rows(inspection, result)
        lines = [f"{name}{DETAIL_ROW_SEPARATOR}{value}" for name, value in rows]
        self.details_label.setText(DETAIL_LINE_SEPARATOR.join(lines))

    def _fill_space(self, result: PreflightResult) -> None:
        """Show the needed-of-free line and fill the bar by the needed share."""
        self.space_label.setText(space_summary(result))
        value = space_bar_value(needed_bytes(result), result.available_bytes)
        self.space_bar.setVisible(value is not None)
        self.space_bar.setValue(value or 0)


def _build_drive_item(candidate: TargetCandidate) -> QListWidgetItem:
    """Build one drive row. A drive without an inspection can't be picked."""
    item = QListWidgetItem(describe_candidate(candidate))
    if candidate.inspection is None:
        blocked = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        item.setFlags(item.flags() & ~blocked)
    return item


def _fill_list(items: QListWidget, lines: list[str]) -> None:
    """Replace the rows of a list with the given lines."""
    items.clear()
    items.addItems(lines)
