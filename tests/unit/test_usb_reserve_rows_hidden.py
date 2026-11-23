"""Behavioral tests for the hidden USB export reserve rows on the Storage tab."""

from __future__ import annotations

from infiniteprep.config.settings import BYTES_PER_MIB, AppSettings
from infiniteprep.ui.settings.storage import StorageTab

STORED_PERCENT = 12.5
STORED_RESERVE_BYTES = 3 * BYTES_PER_MIB + 1
# A partial MiB rounds up. A save never shrinks the stored reserve.
ROUNDED_RESERVE_BYTES = 4 * BYTES_PER_MIB


def test_reserve_rows_stay_hidden(qtbot) -> None:
    """The Storage tab hides both reserve rows. USB export has no entry point."""
    tab = StorageTab(AppSettings())
    qtbot.addWidget(tab)

    assert tab.form.isRowVisible(tab.usb_reserve_percent_spin) is False
    assert tab.form.isRowVisible(tab.usb_reserve_mib_spin) is False
    assert tab.form.isRowVisible(tab.usb_recovery_expiry_checkbox) is True


def test_hidden_rows_keep_the_stored_reserve(qtbot) -> None:
    """A save from the tab keeps the stored percentage and rounds the size up to a MiB."""
    stored = AppSettings(usb_reserve_percent=STORED_PERCENT, usb_reserve_bytes=STORED_RESERVE_BYTES)
    tab = StorageTab(stored)
    qtbot.addWidget(tab)
    saved = AppSettings()

    tab.apply_to(saved)

    assert saved.usb_reserve_percent == STORED_PERCENT
    assert saved.usb_reserve_bytes == ROUNDED_RESERVE_BYTES
