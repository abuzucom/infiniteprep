"""Tests for the USB export confirm dialog (D93, D130, D131)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QListWidget

from infiniteprep.services.usb.models import DriveInspection, FilesystemHealth
from infiniteprep.services.usb.preflight import (
    FACT_ACTIVE_PARTITION,
    PreflightRequest,
    evaluate_preflight,
)
from infiniteprep.ui.usb_preflight_dialog import (
    FIELD_HARDWARE_ID,
    FIELD_MODEL,
    FIELD_MOUNT_PATH,
    FIELD_VOLUME_LABEL,
    FIELD_VOLUME_SERIAL,
    NOT_AVAILABLE_TEXT,
    SPACE_ESTIMATE,
    SPACE_FREE,
    SPACE_REQUIRED,
    SPACE_RESERVE,
    UsbPreflightDialog,
    format_byte_count,
)
from tests.unit.usb_preflight_support import (
    FREE_BYTES,
    MOUNT_PATH,
    build_drive,
    build_inspection,
    build_partition,
    build_request,
)

MARKUP_LABEL = "<b>STICK</b>"
ONE_KIB = 1024
FIVE_GIB = 5 * 1024**3


def open_dialog(
    qtbot, inspection: DriveInspection, request: PreflightRequest | None = None
) -> UsbPreflightDialog:
    """Evaluate the preflight and open the confirm dialog for the result."""
    request = request or build_request()
    dialog = UsbPreflightDialog(inspection, request, evaluate_preflight(inspection, request))
    qtbot.addWidget(dialog)
    return dialog


def list_texts(widget: QListWidget) -> list[str]:
    """Return the text of every row in a list widget."""
    return [widget.item(row).text() for row in range(widget.count())]


@pytest.mark.parametrize(
    ("byte_count", "expected"),
    [
        (0, "0 bytes"),
        (ONE_KIB - 1, "1,023 bytes"),
        (ONE_KIB, "1.0 KiB (1,024 bytes)"),
        (FIVE_GIB, "5.0 GiB (5,368,709,120 bytes)"),
    ],
)
def test_byte_counts_show_a_unit_and_the_exact_value(byte_count: int, expected: str) -> None:
    """A byte count shows a binary unit with one decimal and the exact byte value."""
    assert format_byte_count(byte_count) == expected


def test_passing_target_shows_identity_profiles_changes_and_space(qtbot) -> None:
    """The dialog names the target, the profiles, the planned changes, and the space figures."""
    dialog = open_dialog(qtbot, build_inspection())
    result = evaluate_preflight(build_inspection(), build_request())

    assert dialog.identity_values[FIELD_VOLUME_LABEL].text() == "STICK"
    assert dialog.identity_values[FIELD_MOUNT_PATH].text() == MOUNT_PATH
    assert dialog.identity_values[FIELD_MODEL].text() == "Ultra USB 3.0"
    assert dialog.identity_values[FIELD_HARDWARE_ID].text() == "0781:5581"
    assert dialog.identity_values[FIELD_VOLUME_SERIAL].text() == "1A2B-3C4D"
    assert list_texts(dialog.profiles_list) == ["CDJ-3000"]
    assert list_texts(dialog.changes_list) == ["Add 2 tracks", "Update 1 playlist"]
    assert dialog.space_values[SPACE_ESTIMATE].text() == format_byte_count(result.estimate_bytes)
    assert dialog.space_values[SPACE_RESERVE].text() == format_byte_count(0)
    assert dialog.space_values[SPACE_REQUIRED].text() == format_byte_count(result.estimate_bytes)
    assert dialog.space_values[SPACE_FREE].text() == format_byte_count(FREE_BYTES)


def test_passing_target_enables_confirm(qtbot) -> None:
    """A target without refusals can be confirmed and shows no refusal rows."""
    dialog = open_dialog(qtbot, build_inspection())

    assert dialog.confirm_btn.isEnabled()
    assert dialog.refusals_list.count() == 0
    assert not dialog.identity_group.isHidden()
    assert not dialog.space_group.isHidden()


def test_confirm_accepts_and_cancel_rejects(qtbot) -> None:
    """Confirm closes the dialog as accepted. Cancel closes it as rejected."""
    confirmed = open_dialog(qtbot, build_inspection())
    confirmed.confirm_btn.click()
    cancelled = open_dialog(qtbot, build_inspection())
    cancelled.cancel_btn.click()

    assert confirmed.result() == QDialog.DialogCode.Accepted
    assert cancelled.result() == QDialog.DialogCode.Rejected


def test_absent_identifiers_show_as_not_available(qtbot) -> None:
    """A missing label, model, hardware ID, or serial shows fixed placeholder text."""
    inspection = build_inspection(
        drive=build_drive(model=None, hardware_id=None),
        partitions=(build_partition(label=None, uuid=None),),
    )
    dialog = open_dialog(qtbot, inspection)

    for field in (FIELD_VOLUME_LABEL, FIELD_MODEL, FIELD_HARDWARE_ID, FIELD_VOLUME_SERIAL):
        assert dialog.identity_values[field].text() == NOT_AVAILABLE_TEXT
    assert dialog.identity_values[FIELD_MOUNT_PATH].text() == MOUNT_PATH


def test_drive_text_renders_as_plain_text(qtbot) -> None:
    """A volume label that holds markup reaches the dialog as literal text."""
    inspection = build_inspection(partitions=(build_partition(label=MARKUP_LABEL),))
    dialog = open_dialog(qtbot, inspection)
    label = dialog.identity_values[FIELD_VOLUME_LABEL]

    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.text() == MARKUP_LABEL
    for value in (*dialog.identity_values.values(), *dialog.space_values.values()):
        assert value.textFormat() == Qt.TextFormat.PlainText


def test_refusal_disables_confirm_and_lists_reason_and_guidance(qtbot) -> None:
    """A refused target cannot be confirmed. Each refusal shows its message and guidance."""
    partition = build_partition(filesystem_health=FilesystemHealth.ERRORS)
    inspection = build_inspection(partitions=(partition,))
    refusal = evaluate_preflight(inspection, build_request()).refusals[0]
    dialog = open_dialog(qtbot, inspection)

    assert not dialog.confirm_btn.isEnabled()
    assert dialog.refusals_list.count() == 1
    assert refusal.message in dialog.refusals_list.item(0).text()
    assert refusal.guidance in dialog.refusals_list.item(0).text()
    assert not dialog.identity_group.isHidden()


def test_every_refusal_gets_a_row(qtbot) -> None:
    """Several refusals show one row each in rule order."""
    partition = build_partition(filesystem="exfat", is_read_only=True)
    inspection = build_inspection(partitions=(partition,), available_bytes=0)
    refusals = evaluate_preflight(inspection, build_request()).refusals
    dialog = open_dialog(qtbot, inspection)

    assert dialog.refusals_list.count() == len(refusals)
    for row, refusal in enumerate(refusals):
        assert refusal.message in dialog.refusals_list.item(row).text()


def test_safety_refusal_hides_identity_and_space(qtbot) -> None:
    """A drive that must never receive an export shows no identity and no space figures."""
    dialog = open_dialog(qtbot, build_inspection(drive=build_drive(is_system=True)))

    assert not dialog.confirm_btn.isEnabled()
    assert dialog.identity_group.isHidden()
    assert dialog.space_group.isHidden()
    assert dialog.refusals_list.count() == 1


def test_unverified_facts_are_listed_and_do_not_block(qtbot) -> None:
    """A fact without a source shows as not verified. Confirm stays enabled."""
    inspection = build_inspection(partitions=(build_partition(is_active=None),))
    result = evaluate_preflight(inspection, build_request())
    dialog = open_dialog(qtbot, inspection)

    assert result.unverified_facts == (FACT_ACTIVE_PARTITION,)
    assert dialog.unverified_list.count() == 1
    assert "active" in dialog.unverified_list.item(0).text().casefold()
    assert dialog.confirm_btn.isEnabled()


def test_verified_target_shows_no_unverified_rows(qtbot) -> None:
    """A fully verified target shows an empty not-verified list."""
    dialog = open_dialog(qtbot, build_inspection())

    assert dialog.unverified_list.count() == 0
