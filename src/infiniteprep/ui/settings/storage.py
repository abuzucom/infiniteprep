"""Storage settings tab for the USB export reserve and the data cleanup action."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Final

from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QWidget,
)

from infiniteprep.config.settings import (
    BYTES_PER_MIB,
    MAX_USB_RECOVERY_EXPIRY_DAYS,
    MAX_USB_RESERVE_MIB,
    MAX_USB_RESERVE_PERCENT,
    MIN_USB_RECOVERY_EXPIRY_DAYS,
    AppSettings,
)
from infiniteprep.services.usb.recovery import UsbRecoveryJournalStore
from infiniteprep.ui.action_handlers import ask_plain_question, show_plain_message
from infiniteprep.ui.settings.base import FormSettingsTab

CLEANUP_BUTTON_LABEL: Final[str] = "Delete InfinitePrep Data..."
RECOVERY_CLEANUP_BUTTON_LABEL: Final[str] = "Clean Up USB Recovery Journal..."
RECOVERY_CLEANUP_TITLE: Final[str] = "Clean Up USB Recovery Journal"
RECOVERY_CLEANUP_QUESTION: Final[str] = (
    "Delete every unresolved USB recovery entry?\n\n"
    "An interrupted USB export can no longer be resumed or restored after this. "
    "This cannot be undone."
)
USB_RESERVE_PERCENT_DECIMALS: Final[int] = 1
logger = logging.getLogger(__name__)


class StorageTab(FormSettingsTab):
    """Settings tab for USB recovery, the hidden USB export reserve, and data cleanup.

    The dialog connects the cleanup button, since a confirmed cleanup closes the
    dialog.
    """

    TITLE = "Storage"

    def __init__(
        self,
        settings: AppSettings,
        cleanup_enabled: bool = True,
        parent: QWidget | None = None,
        *,
        journal_store: UsbRecoveryJournalStore | None = None,
    ) -> None:
        """Initialize the tab from the stored settings.

        A false cleanup_enabled disables the cleanup button. journal_store names the
        USB recovery journal the cleanup button clears. The default store uses the
        application journal path.
        """
        super().__init__(parent)
        self._journal_store = journal_store
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
        # USB export has no entry point yet. The reserve rows stay hidden until then.
        # The hidden inputs still hold the stored values. A save keeps those values.
        self.form.setRowVisible(self.usb_reserve_percent_spin, False)
        self.form.setRowVisible(self.usb_reserve_mib_spin, False)

        self.usb_recovery_expiry_checkbox = QCheckBox(
            "Delete unresolved USB recovery data automatically",
            self,
        )
        self.usb_recovery_expiry_checkbox.setChecked(settings.usb_recovery_expiry_enabled)
        self.form.addRow("USB Recovery Expiry:", self.usb_recovery_expiry_checkbox)

        self.usb_recovery_expiry_days_spin = QSpinBox(self)
        self.usb_recovery_expiry_days_spin.setRange(
            MIN_USB_RECOVERY_EXPIRY_DAYS,
            MAX_USB_RECOVERY_EXPIRY_DAYS,
        )
        self.usb_recovery_expiry_days_spin.setValue(settings.usb_recovery_expiry_days)
        self.form.addRow("USB Recovery Expiry (days):", self.usb_recovery_expiry_days_spin)

        self.usb_recovery_cleanup_btn = QPushButton(RECOVERY_CLEANUP_BUTTON_LABEL, self)
        self.usb_recovery_cleanup_btn.clicked.connect(self._cleanup_usb_recovery_journal)
        self.form.addRow("USB Recovery Journal:", self.usb_recovery_cleanup_btn)

        self.cleanup_btn = QPushButton(CLEANUP_BUTTON_LABEL, self)
        self.cleanup_btn.setEnabled(cleanup_enabled)
        self.form.addRow("Application Data:", self.cleanup_btn)

    def clean_usb_recovery_journal(
        self,
        *,
        journal_store: UsbRecoveryJournalStore | None = None,
        show_error: Callable[[str, str], None] | None = None,
    ) -> int | None:
        """Remove unresolved USB recovery journal entries while guarding corrupt data."""
        store = journal_store or UsbRecoveryJournalStore()
        try:
            removed = store.cleanup_unresolved()
        except (OSError, ValueError) as exc:
            logger.warning("Manual USB recovery cleanup failed: %s", type(exc).__name__)
            message = "Unable to read the USB recovery journal. Existing data was preserved."
            self.usb_recovery_cleanup_btn.setToolTip(message)
            if show_error is not None:
                show_error("USB Recovery Journal Error", message)
            else:
                QMessageBox.warning(self, "USB Recovery Journal Error", message)
            return None
        count = len(removed)
        self.usb_recovery_cleanup_btn.setToolTip(
            f"Removed {count} unresolved USB recovery entry(s)."
        )
        return count

    def _cleanup_usb_recovery_journal(self) -> None:
        """Ask first, then remove unresolved USB recovery journal entries (D193).

        The removal ends every resume and restore path for those exports, so it waits
        for a Yes. The result shows in a message, since a tooltip never reaches a
        keyboard or screen reader user.
        """
        if not ask_plain_question(self, RECOVERY_CLEANUP_TITLE, RECOVERY_CLEANUP_QUESTION):
            return
        count = self.clean_usb_recovery_journal(journal_store=self._journal_store)
        if count is None:
            return
        show_plain_message(
            self,
            QMessageBox.Icon.Information,
            RECOVERY_CLEANUP_TITLE,
            f"Removed {count} unresolved USB recovery entry(s).",
        )

    def apply_to(self, settings: AppSettings) -> None:
        """Copy USB reserve and recovery expiry inputs into settings."""
        settings.usb_reserve_percent = self.usb_reserve_percent_spin.value()
        settings.usb_reserve_bytes = self.usb_reserve_mib_spin.value() * BYTES_PER_MIB
        settings.usb_recovery_expiry_enabled = self.usb_recovery_expiry_checkbox.isChecked()
        settings.usb_recovery_expiry_days = self.usb_recovery_expiry_days_spin.value()


TAB_CLASS = StorageTab
