"""Drive picker for a USB export target (D93, D188).

The picker lists removable-class drives only. Drive text is untrusted and
renders as plain text. Raw command errors never reach the dialog.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.services.usb.models import DriveInspection, DriveInspectionError
from infiniteprep.services.usb.preflight import (
    SAFETY_REFUSAL_CODES,
    PreflightRequest,
    evaluate_preflight,
)
from infiniteprep.services.usb.targets import TargetCandidate, load_removable_targets

logger = logging.getLogger(__name__)

USB_DRIVE_PICKER_WIDTH: Final[int] = 620
USB_DRIVE_PICKER_HEIGHT: Final[int] = 360
LOAD_FAILED_TEXT: Final[str] = "The drive list failed to load. Reconnect the drive and rescan."
NO_DRIVES_TEXT: Final[str] = "No removable drives found. Connect a drive and rescan."
SELECT_PROMPT_TEXT: Final[str] = "Select the export drive."
NOT_MOUNTED_TEXT: Final[str] = "not mounted"
ROW_SEPARATOR: Final[str] = " - "
# The picker shows drive-level refusals only. The export plan comes later.
EMPTY_REQUEST: Final[PreflightRequest] = PreflightRequest(profiles=(), file_sizes=())

TargetLoader = Callable[[], tuple[TargetCandidate, ...]]


def describe_candidate(candidate: TargetCandidate) -> str:
    """Return the row text of one candidate.

    A safety refusal shows the drive name and the reason only.
    """
    inspection = candidate.inspection
    if inspection is None:
        return ROW_SEPARATOR.join((candidate.name, candidate.error or ""))
    refusals = evaluate_preflight(inspection, EMPTY_REQUEST).refusals
    if refusals and refusals[0].code in SAFETY_REFUSAL_CODES:
        return ROW_SEPARATOR.join((candidate.name, refusals[0].message))
    partition = inspection.partitions[0] if inspection.partitions else None
    title = (partition.label if partition else None) or inspection.drive.model or candidate.name
    mount_points = partition.mount_points if partition else ()
    parts = [title, mount_points[0] if mount_points else NOT_MOUNTED_TEXT]
    if refusals:
        parts.append(refusals[0].message)
    return ROW_SEPARATOR.join(parts)


class UsbDrivePickerDialog(QDialog):
    """Modal dialog listing removable-class drives for one export target choice."""

    def __init__(
        self,
        loader: TargetLoader = load_removable_targets,
        parent: QWidget | None = None,
    ) -> None:
        """Build the picker and load the drive list once."""
        super().__init__(parent)
        self._loader = loader
        self._candidates: tuple[TargetCandidate, ...] = ()
        self.setWindowTitle("Select USB Export Drive")
        self.resize(USB_DRIVE_PICKER_WIDTH, USB_DRIVE_PICKER_HEIGHT)

        self.drive_list = QListWidget(self)
        self.status_label = QLabel(self)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.rescan_btn = QPushButton("Rescan", self)
        self.select_btn = QPushButton("Select Drive", self)
        self.cancel_btn = QPushButton("Cancel", self)

        self._build_layout()
        self.reload()

    def _build_layout(self) -> None:
        """Assemble the list, the status line, and the buttons."""
        root = QVBoxLayout(self)
        root.addWidget(self.drive_list, stretch=1)
        root.addWidget(self.status_label)
        buttons = QHBoxLayout()
        buttons.addWidget(self.rescan_btn)
        buttons.addStretch()
        buttons.addWidget(self.select_btn)
        buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)
        self.drive_list.currentRowChanged.connect(self._update_select_state)
        self.rescan_btn.clicked.connect(self.reload)
        self.select_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

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
            self._update_select_state()
            return
        self._candidates = candidates
        for candidate in candidates:
            self.drive_list.addItem(self._build_item(candidate))
        self.status_label.setText(SELECT_PROMPT_TEXT if candidates else NO_DRIVES_TEXT)
        self._update_select_state()

    def _build_item(self, candidate: TargetCandidate) -> QListWidgetItem:
        """Build one list row. A drive without an inspection cannot be chosen."""
        item = QListWidgetItem(describe_candidate(candidate))
        if candidate.inspection is None:
            disabled = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            item.setFlags(item.flags() & ~disabled)
        return item

    def selected_inspection(self) -> DriveInspection | None:
        """Return the inspection of the chosen row, or None without a valid choice."""
        row = self.drive_list.currentRow()
        if row < 0 or row >= len(self._candidates):
            return None
        return self._candidates[row].inspection

    def _update_select_state(self) -> None:
        """Enable the select button only for a row that holds an inspection."""
        self.select_btn.setEnabled(self.selected_inspection() is not None)
