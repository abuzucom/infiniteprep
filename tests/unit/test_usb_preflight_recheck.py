"""Unit tests for the pre-write USB target recheck (D130, D132)."""

from __future__ import annotations

import unittest

from infiniteprep.services.usb.models import DriveInspection, FilesystemHealth
from infiniteprep.services.usb.preflight import RefusalCode
from infiniteprep.services.usb.recheck import (
    AbortCode,
    RecheckResult,
    capture_target_snapshot,
    recheck_target,
)
from infiniteprep.services.usb.space import ReservePolicy, estimate_export_bytes
from tests.unit.usb_preflight_support import (
    MOUNT_PATH,
    build_drive,
    build_inspection,
    build_partition,
    build_request,
)


def report_writable(_path: str) -> bool:
    """Report every mount path as writable."""
    return True


def report_not_writable(_path: str) -> bool:
    """Report every mount path as not writable."""
    return False


def recheck_against(fresh: DriveInspection) -> RecheckResult:
    """Recheck a fresh inspection against a snapshot of the passing stick."""
    snapshot = capture_target_snapshot(build_inspection())
    return recheck_target(snapshot, fresh, build_request(), is_path_writable=report_writable)


def abort_codes(result: RecheckResult) -> tuple[AbortCode, ...]:
    """Return the abort codes of a recheck result in order."""
    return tuple(reason.code for reason in result.abort_reasons)


class SnapshotTests(unittest.TestCase):
    """The snapshot records the confirmed mount path."""

    def test_snapshot_records_the_mount_path(self) -> None:
        self.assertEqual(capture_target_snapshot(build_inspection()).mount_path, MOUNT_PATH)

    def test_snapshot_of_an_unmounted_target_is_rejected(self) -> None:
        inspection = build_inspection(partitions=(build_partition(mount_points=()),))
        with self.assertRaises(ValueError):
            capture_target_snapshot(inspection)


class UnchangedTargetTests(unittest.TestCase):
    """An unchanged target may be written."""

    def test_unchanged_target_passes(self) -> None:
        result = recheck_against(build_inspection())
        self.assertEqual(result.abort_reasons, ())
        self.assertTrue(result.may_write)

    def test_device_node_rename_alone_does_not_abort(self) -> None:
        fresh = build_inspection(
            drive=build_drive(name="sdc", path="/dev/sdc"),
            partitions=(build_partition(name="sdc1", path="/dev/sdc1"),),
        )
        self.assertTrue(recheck_against(fresh).may_write)

    def test_harmless_free_space_change_does_not_abort(self) -> None:
        self.assertTrue(recheck_against(build_inspection(available_bytes=15_000_000_000)).may_write)


class ChangedIdentityTests(unittest.TestCase):
    """Any changed identifier aborts the export."""

    def assert_identity_abort(self, fresh: DriveInspection, field_name: str) -> None:
        """Assert an identity abort whose message names the changed field."""
        result = recheck_against(fresh)
        self.assertFalse(result.may_write)
        self.assertEqual(abort_codes(result), (AbortCode.IDENTITY_CHANGED,))
        self.assertIn(field_name, result.abort_reasons[0].message)

    def test_each_changed_drive_identifier_aborts(self) -> None:
        changes = {
            "serial": "FFFF000011112222",
            "hardware_id": "0951:1666",
            "model": "DataTraveler",
            "vendor": "Kingston",
            "size_bytes": 64_000_000_000,
        }
        for field_name, value in changes.items():
            with self.subTest(field_name=field_name):
                fresh = build_inspection(drive=build_drive(**{field_name: value}))
                self.assert_identity_abort(fresh, field_name)

    def test_each_changed_partition_identifier_aborts(self) -> None:
        changes = {
            "uuid": "9999-0000",
            "label": "OTHER",
            "size_bytes": 31_000_000_000,
            "partition_number": 2,
            "partition_type": "0xb",
        }
        for field_name, value in changes.items():
            with self.subTest(field_name=field_name):
                fresh = build_inspection(partitions=(build_partition(**{field_name: value}),))
                self.assert_identity_abort(fresh, field_name)

    def test_identifier_that_disappears_aborts(self) -> None:
        self.assert_identity_abort(build_inspection(drive=build_drive(serial=None)), "serial")

    def test_identifier_that_appears_aborts(self) -> None:
        snapshot = capture_target_snapshot(build_inspection(drive=build_drive(serial=None)))
        result = recheck_target(
            snapshot, build_inspection(), build_request(), is_path_writable=report_writable
        )
        self.assertEqual(abort_codes(result), (AbortCode.IDENTITY_CHANGED,))

    def test_abort_message_omits_identifier_values(self) -> None:
        result = recheck_against(build_inspection(drive=build_drive(serial="FFFF000011112222")))
        message = result.abort_reasons[0].message
        self.assertNotIn("FFFF000011112222", message)
        self.assertNotIn("0401568289410948", message)


class ChangedStateTests(unittest.TestCase):
    """Mount path, writability, safety, and space changes abort the export."""

    def test_changed_mount_path_aborts(self) -> None:
        other_mount = "/media/dj/STICK1"
        fresh = build_inspection(partitions=(build_partition(mount_points=(other_mount,)),))
        result = recheck_against(fresh)
        self.assertIn(AbortCode.MOUNT_PATH_CHANGED, abort_codes(result))
        self.assertFalse(result.may_write)

    def test_unwritable_mount_path_aborts(self) -> None:
        snapshot = capture_target_snapshot(build_inspection())
        result = recheck_target(
            snapshot, build_inspection(), build_request(), is_path_writable=report_not_writable
        )
        self.assertEqual(abort_codes(result), (AbortCode.NOT_WRITABLE,))

    def test_read_only_remount_aborts_through_the_policy(self) -> None:
        fresh = build_inspection(partitions=(build_partition(is_read_only=True),))
        result = recheck_against(fresh)
        self.assertEqual(abort_codes(result), (AbortCode.PREFLIGHT_REFUSED,))
        codes = tuple(refusal.code for refusal in result.preflight.refusals)
        self.assertEqual(codes, (RefusalCode.READ_ONLY_MEDIA,))

    def test_new_filesystem_errors_abort(self) -> None:
        partition = build_partition(filesystem_health=FilesystemHealth.ERRORS)
        result = recheck_against(build_inspection(partitions=(partition,)))
        self.assertEqual(abort_codes(result), (AbortCode.PREFLIGHT_REFUSED,))

    def test_space_that_became_insufficient_aborts(self) -> None:
        request = build_request()
        estimate = estimate_export_bytes(
            request.file_sizes, request.directory_count, request.database_allowance_bytes
        )
        snapshot = capture_target_snapshot(build_inspection())
        fresh = build_inspection(available_bytes=estimate + 99)
        result = recheck_target(
            snapshot,
            fresh,
            request,
            ReservePolicy(fixed_bytes=100),
            is_path_writable=report_writable,
        )
        self.assertEqual(abort_codes(result), (AbortCode.PREFLIGHT_REFUSED,))
        codes = tuple(refusal.code for refusal in result.preflight.refusals)
        self.assertEqual(codes, (RefusalCode.INSUFFICIENT_SPACE,))

    def test_changed_identity_and_state_report_both(self) -> None:
        fresh = build_inspection(
            drive=build_drive(serial="FFFF000011112222"),
            partitions=(build_partition(is_read_only=True),),
        )
        self.assertEqual(
            abort_codes(recheck_against(fresh)),
            (AbortCode.IDENTITY_CHANGED, AbortCode.PREFLIGHT_REFUSED),
        )


if __name__ == "__main__":
    unittest.main()
