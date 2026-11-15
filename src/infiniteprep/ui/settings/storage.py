"""Storage settings tab for the USB export reserve and the data cleanup action."""

from __future__ import annotations

from typing import Final

from PySide6.QtWidgets import QDoubleSpinBox, QPushButton, QSpinBox, QWidget

from infiniteprep.config.settings import (
    BYTES_PER_MIB,
    MAX_USB_RESERVE_MIB,
    MAX_USB_RESERVE_PERCENT,
    AppSettings,
)
from infiniteprep.ui.settings.base import FormSettingsTab

CLEANUP_BUTTON_LABEL: Final[str] = "Delete InfinitePrep Data..."
USB_RESERVE_PERCENT_DECIMALS: Final[int] = 1


class StorageTab(FormSettingsTab):
    """Settings tab for the USB export reserve and the data cleanup button.

    The dialog connects the cleanup button, since a confirmed cleanup closes the
    dialog.
    """

    TITLE = "Storage"

    def __init__(
        self,
        settings: AppSettings,
        cleanup_enabled: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the tab from the stored settings.

        A false cleanup_enabled disables the cleanup button.
        """
        super().__init__(parent)
        self.usb_reserve_percent_spin = QDoubleSpinBox(self)
        self.usb_reserve_percent_spin.setDecimals(USB_RESERVE_PERCENT_DECIMALS)
        self.usb_reserve_percent_spin.setRange(0.0, MAX_USB_RESERVE_PERCENT)
        self.usb_reserve_percent_spin.setValue(settings.usb_reserve_percent)
        self.form.addRow("USB Export Reserve (% of export size):", self.usb_reserve_percent_spin)

        self.usb_reserve_mib_spin = QSpinBox(self)
        self.usb_reserve_mib_spin.setRange(0, MAX_USB_RESERVE_MIB)
        # A partial MiB rounds up. A save never shrinks the stored reserve.
        self.usb_reserve_mib_spin.setValue(-(-settings.usb_reserve_bytes // BYTES_PER_MIB))
        self.form.addRow("USB Export Reserve (MiB):", self.usb_reserve_mib_spin)

        self.cleanup_btn = QPushButton(CLEANUP_BUTTON_LABEL, self)
        self.cleanup_btn.setEnabled(cleanup_enabled)
        self.form.addRow("Application Data:", self.cleanup_btn)

    def apply_to(self, settings: AppSettings) -> None:
        """Copy both reserve inputs into settings. The larger reserve kind applies."""
        settings.usb_reserve_percent = self.usb_reserve_percent_spin.value()
        settings.usb_reserve_bytes = self.usb_reserve_mib_spin.value() * BYTES_PER_MIB


TAB_CLASS = StorageTab
