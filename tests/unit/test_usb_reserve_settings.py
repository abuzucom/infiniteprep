"""Tests for the USB export reserve settings and their Settings inputs (D97 to D99, D199)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import (
    BYTES_PER_MIB,
    MAX_USB_RESERVE_MIB,
    MAX_USB_RESERVE_PERCENT,
    AppSettings,
    SettingsManager,
)
from infiniteprep.services.usb.space import ReservePolicy, compute_reserve_bytes
from infiniteprep.ui.settings_dialog import SettingsDialog

STORED_PERCENT = 12.5
STORED_RESERVE_MIB = 3
STORED_RESERVE_BYTES = STORED_RESERVE_MIB * BYTES_PER_MIB
ENTERED_PERCENT = 7.5
ENTERED_RESERVE_MIB = 256
ESTIMATE_BYTES = 1_000_000_000
PERCENT_RESERVE_OF_ESTIMATE = 125_000_000


def write_settings(path: Path, values: dict[str, object]) -> SettingsManager:
    """Write a settings file with the given values and return its manager."""
    path.write_text(json.dumps(values), encoding="utf-8")
    return SettingsManager(path)


def open_dialog(qtbot, manager: SettingsManager) -> SettingsDialog:
    """Open a Settings dialog over an in-memory credential store."""
    dialog = SettingsDialog(manager, CredentialStore(use_memory_backend=True))
    qtbot.addWidget(dialog)
    return dialog


def test_both_reserve_kinds_start_at_zero(tmp_path: Path) -> None:
    """New settings and older settings files hold a zero reserve of both kinds."""
    manager = write_settings(tmp_path / "settings.json", {"transcode_mode": "sibling_export"})

    assert AppSettings().usb_reserve_percent == 0.0
    assert AppSettings().usb_reserve_bytes == 0
    assert manager.load().usb_reserve_percent == 0.0
    assert manager.load().usb_reserve_bytes == 0


def test_valid_stored_reserve_values_load(tmp_path: Path) -> None:
    """A stored percentage and byte count load unchanged."""
    values = {"usb_reserve_percent": STORED_PERCENT, "usb_reserve_bytes": STORED_RESERVE_BYTES}
    settings = write_settings(tmp_path / "settings.json", values).load()

    assert settings.usb_reserve_percent == STORED_PERCENT
    assert settings.usb_reserve_bytes == STORED_RESERVE_BYTES


@pytest.mark.parametrize(
    "stored_percent",
    [-1, MAX_USB_RESERVE_PERCENT + 1, "ten", True, None, float("nan"), float("inf"), [5]],
)
def test_invalid_stored_percent_loads_as_zero(tmp_path: Path, stored_percent: object) -> None:
    """A percentage outside 0 to 100 or of another type loads as zero."""
    manager = write_settings(tmp_path / "settings.json", {"usb_reserve_percent": stored_percent})

    assert manager.load().usb_reserve_percent == 0.0


@pytest.mark.parametrize(
    "stored_bytes",
    [-1, 1.5, "many", True, None, MAX_USB_RESERVE_MIB * BYTES_PER_MIB + 1, [5]],
)
def test_invalid_stored_bytes_load_as_zero(tmp_path: Path, stored_bytes: object) -> None:
    """A byte count below zero, above the limit, or of another type loads as zero."""
    manager = write_settings(tmp_path / "settings.json", {"usb_reserve_bytes": stored_bytes})

    assert manager.load().usb_reserve_bytes == 0


def test_stored_reserve_feeds_the_reserve_policy(tmp_path: Path) -> None:
    """The loaded values build a reserve policy. The larger kind decides."""
    values = {"usb_reserve_percent": STORED_PERCENT, "usb_reserve_bytes": STORED_RESERVE_BYTES}
    settings = write_settings(tmp_path / "settings.json", values).load()
    reserve = ReservePolicy(
        percent=settings.usb_reserve_percent, fixed_bytes=settings.usb_reserve_bytes
    )

    assert compute_reserve_bytes(ESTIMATE_BYTES, reserve) == PERCENT_RESERVE_OF_ESTIMATE


def test_settings_dialog_shows_the_stored_reserve(qtbot, tmp_path: Path) -> None:
    """The inputs open with the stored percentage and the stored size in MiB."""
    values = {"usb_reserve_percent": STORED_PERCENT, "usb_reserve_bytes": STORED_RESERVE_BYTES}
    dialog = open_dialog(qtbot, write_settings(tmp_path / "settings.json", values))

    assert dialog.usb_reserve_percent_spin.value() == STORED_PERCENT
    assert dialog.usb_reserve_mib_spin.value() == STORED_RESERVE_MIB


def test_settings_dialog_rounds_a_partial_mib_up(qtbot, tmp_path: Path) -> None:
    """A stored size between two MiB values shows the higher one. A save never shrinks it."""
    values = {"usb_reserve_bytes": STORED_RESERVE_BYTES + 1}
    dialog = open_dialog(qtbot, write_settings(tmp_path / "settings.json", values))

    assert dialog.usb_reserve_mib_spin.value() == STORED_RESERVE_MIB + 1


def test_settings_dialog_input_ranges(qtbot, tmp_path: Path) -> None:
    """The percentage runs from 0 to 100 with one decimal. The size has a fixed upper bound."""
    dialog = open_dialog(qtbot, SettingsManager(tmp_path / "settings.json"))

    assert dialog.usb_reserve_percent_spin.minimum() == 0.0
    assert dialog.usb_reserve_percent_spin.maximum() == MAX_USB_RESERVE_PERCENT
    assert dialog.usb_reserve_percent_spin.decimals() == 1
    assert dialog.usb_reserve_mib_spin.minimum() == 0
    assert dialog.usb_reserve_mib_spin.maximum() == MAX_USB_RESERVE_MIB


def test_settings_dialog_saves_both_reserve_kinds(qtbot, tmp_path: Path) -> None:
    """Saving stores the percentage and the size in bytes."""
    manager = SettingsManager(tmp_path / "settings.json")
    dialog = open_dialog(qtbot, manager)
    assert dialog.usb_reserve_percent_spin.value() == 0.0
    assert dialog.usb_reserve_mib_spin.value() == 0

    dialog.usb_reserve_percent_spin.setValue(ENTERED_PERCENT)
    dialog.usb_reserve_mib_spin.setValue(ENTERED_RESERVE_MIB)
    dialog.save_settings()

    saved = manager.load()
    assert saved.usb_reserve_percent == ENTERED_PERCENT
    assert saved.usb_reserve_bytes == ENTERED_RESERVE_MIB * BYTES_PER_MIB
