"""Final confirm dialog for a USB export target (D93, D130, D131).

Drive labels, models, and serials come from the drive and are untrusted. Every
label renders as plain text.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.services.usb.models import DriveInspection
from infiniteprep.services.usb.preflight import (
    FACT_ACTIVE_PARTITION,
    FACT_FILESYSTEM_HEALTH,
    FACT_PRIMARY_PARTITION,
    FACT_SYSTEM_VOLUME,
    SAFETY_REFUSAL_CODES,
    PreflightRequest,
    PreflightResult,
)

USB_PREFLIGHT_DIALOG_WIDTH: Final[int] = 640
USB_PREFLIGHT_DIALOG_HEIGHT: Final[int] = 620
FIELD_VOLUME_LABEL: Final[str] = "Volume label"
FIELD_MOUNT_PATH: Final[str] = "Mount path"
FIELD_MODEL: Final[str] = "Model"
FIELD_HARDWARE_ID: Final[str] = "Hardware ID"
FIELD_VOLUME_SERIAL: Final[str] = "Volume serial"
SPACE_ESTIMATE: Final[str] = "Estimated export size"
SPACE_RESERVE: Final[str] = "Reserve"
SPACE_REQUIRED: Final[str] = "Required space"
SPACE_FREE: Final[str] = "Free space"
NOT_AVAILABLE_TEXT: Final[str] = "Not available"
NOT_VERIFIED_SUFFIX: Final[str] = " (not verified)"
FACT_DISPLAY_NAMES: Final[dict[str, str]] = {
    FACT_ACTIVE_PARTITION: "Active boot partition flag",
    FACT_FILESYSTEM_HEALTH: "Filesystem health",
    FACT_PRIMARY_PARTITION: "Primary partition layout",
    FACT_SYSTEM_VOLUME: "System volume state",
}
BYTES_PER_UNIT_STEP: Final[int] = 1024
BYTE_UNITS: Final[tuple[str, ...]] = ("KiB", "MiB", "GiB")
LARGEST_BYTE_UNIT: Final[str] = "TiB"


def format_byte_count(byte_count: int) -> str:
    """Return a byte count with a binary unit and the exact byte value."""
    exact = f"{byte_count:,} bytes"
    if byte_count < BYTES_PER_UNIT_STEP:
        return exact
    value = float(byte_count)
    for unit in BYTE_UNITS:
        value /= BYTES_PER_UNIT_STEP
        if value < BYTES_PER_UNIT_STEP:
            return f"{value:.1f} {unit} ({exact})"
    value /= BYTES_PER_UNIT_STEP
    return f"{value:.1f} {LARGEST_BYTE_UNIT} ({exact})"


def _read_identity_fields(inspection: DriveInspection, mount_path: str | None) -> dict[str, str]:
    """Return the display text of each target identity field (D93)."""
    partition = inspection.partitions[0] if inspection.partitions else None
    values = {
        FIELD_VOLUME_LABEL: partition.label if partition else None,
        FIELD_MOUNT_PATH: mount_path,
        FIELD_MODEL: inspection.drive.model,
        FIELD_HARDWARE_ID: inspection.drive.hardware_id,
        FIELD_VOLUME_SERIAL: partition.uuid if partition else None,
    }
    return {name: value or NOT_AVAILABLE_TEXT for name, value in values.items()}


def _read_space_fields(result: PreflightResult) -> dict[str, str]:
    """Return the display text of each space figure."""
    free_text = NOT_AVAILABLE_TEXT
    if result.available_bytes is not None:
        free_text = format_byte_count(result.available_bytes)
    return {
        SPACE_ESTIMATE: format_byte_count(result.estimate_bytes),
        SPACE_RESERVE: format_byte_count(result.reserve_bytes),
        SPACE_REQUIRED: format_byte_count(result.estimate_bytes + result.reserve_bytes),
        SPACE_FREE: free_text,
    }


class UsbPreflightDialog(QDialog):
    """Modal dialog naming the target, the profiles, the changes, and the space estimate.

    Refusals disable the confirm button. Unverified facts stay visible and do
    not block. A safety refusal hides the identity and space sections.
    """

    def __init__(
        self,
        inspection: DriveInspection,
        request: PreflightRequest,
        result: PreflightResult,
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog from one inspection, one request, and its preflight result."""
        super().__init__(parent)
        self.setWindowTitle("Confirm USB Export")
        self.resize(USB_PREFLIGHT_DIALOG_WIDTH, USB_PREFLIGHT_DIALOG_HEIGHT)

        self.identity_group = QGroupBox("Target drive", self)
        self.space_group = QGroupBox("Space", self)
        self.identity_values = self._fill_group(
            self.identity_group, _read_identity_fields(inspection, result.mount_path)
        )
        self.space_values = self._fill_group(self.space_group, _read_space_fields(result))
        self.profiles_list = QListWidget(self)
        self.changes_list = QListWidget(self)
        self.refusals_list = QListWidget(self)
        self.unverified_list = QListWidget(self)
        self.notes_list = QListWidget(self)
        self.confirm_btn = QPushButton("Confirm Export", self)
        self.cancel_btn = QPushButton("Cancel", self)

        self._fill_lists(request, result)
        self._build_layout()
        self._apply_result_state(result)

    def _fill_group(self, group: QGroupBox, fields: dict[str, str]) -> dict[str, QLabel]:
        """Add one plain-text value row per field to a group and return the value labels."""
        form = QFormLayout(group)
        labels: dict[str, QLabel] = {}
        for name, text in fields.items():
            value_label = QLabel(self)
            # PlainText keeps markup in drive-supplied text from rendering.
            value_label.setTextFormat(Qt.TextFormat.PlainText)
            value_label.setText(text)
            form.addRow(f"{name}:", value_label)
            labels[name] = value_label
        return labels

    def _fill_lists(self, request: PreflightRequest, result: PreflightResult) -> None:
        """Fill the profile, change, refusal, and not-verified lists."""
        self.profiles_list.addItems([profile.name for profile in request.profiles])
        self.changes_list.addItems(list(request.planned_changes))
        self.refusals_list.addItems(
            [f"{refusal.message} {refusal.guidance}".strip() for refusal in result.refusals]
        )
        self.notes_list.addItems([note.message for note in result.notes])
        self.unverified_list.addItems(
            [
                FACT_DISPLAY_NAMES.get(name, name) + NOT_VERIFIED_SUFFIX
                for name in result.unverified_facts
            ]
        )

    def _build_layout(self) -> None:
        """Assemble the sections in reading order."""
        root = QVBoxLayout(self)
        root.addWidget(self.identity_group)
        self._add_titled_list(root, "DJ profiles", self.profiles_list)
        self._add_titled_list(root, "Planned changes", self.changes_list)
        root.addWidget(self.space_group)
        self._add_titled_list(root, "Refusals", self.refusals_list)
        self._add_titled_list(root, "Not verified", self.unverified_list)
        self._add_titled_list(root, "Notes", self.notes_list)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.confirm_btn)
        buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)
        self.confirm_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

    def _add_titled_list(self, root: QVBoxLayout, title: str, widget: QListWidget) -> None:
        """Add a fixed section title and its list to the layout."""
        root.addWidget(QLabel(title, self))
        root.addWidget(widget)

    def _apply_result_state(self, result: PreflightResult) -> None:
        """Disable confirm on any refusal and hide drive detail on a safety refusal."""
        self.confirm_btn.setEnabled(result.passed)
        has_safety_refusal = any(
            refusal.code in SAFETY_REFUSAL_CODES for refusal in result.refusals
        )
        self.identity_group.setVisible(not has_safety_refusal)
        self.space_group.setVisible(not has_safety_refusal)
