"""Unit tests for the USB preflight policy (D94, D96, D131, D137, D138, D184 to D189, D202)."""

from __future__ import annotations

import unittest

from infiniteprep.services.usb.models import DriveInspection, FilesystemHealth
from infiniteprep.services.usb.preflight import (
    FACT_ACTIVE_PARTITION,
    FACT_FILESYSTEM_HEALTH,
    FACT_PRIMARY_PARTITION,
    FAT32_BLOCKED_FILE_BYTES,
    SAFETY_REFUSAL_CODES,
    DjProfileRequest,
    PreflightRequest,
    PreflightResult,
    RefusalCode,
    evaluate_preflight,
)
from infiniteprep.services.usb.space import (
    ReservePolicy,
    compute_reserve_bytes,
    estimate_export_bytes,
)
from tests.unit.usb_preflight_support import (
    MOUNT_PATH,
    build_drive,
    build_inspection,
    build_partition,
    build_request,
)


def refusal_codes(result: PreflightResult) -> tuple[RefusalCode, ...]:
    """Return the refusal codes of a result in order."""
    return tuple(refusal.code for refusal in result.refusals)


def evaluate_default(inspection: DriveInspection) -> PreflightResult:
    """Evaluate an inspection against the passing request with no reserve."""
    return evaluate_preflight(inspection, build_request())


class PassingTargetTests(unittest.TestCase):
    """A removable FAT32 stick with one primary MBR partition passes."""

    def test_passing_stick_has_no_refusals(self) -> None:
        result = evaluate_default(build_inspection())
        self.assertEqual(result.refusals, ())
        self.assertTrue(result.passed)
        self.assertEqual(result.unverified_facts, ())

    def test_result_reports_space_figures_and_mount_path(self) -> None:
        request = build_request()
        result = evaluate_preflight(build_inspection(available_bytes=10**9), request)
        expected = estimate_export_bytes(
            request.file_sizes, request.directory_count, request.database_allowance_bytes
        )
        self.assertEqual(result.estimate_bytes, expected)
        self.assertEqual(result.reserve_bytes, 0)
        self.assertEqual(result.available_bytes, 10**9)
        self.assertEqual(result.mount_path, MOUNT_PATH)

    def test_scheme_and_filesystem_names_from_each_platform_pass(self) -> None:
        for scheme, filesystem in (("dos", "vfat"), ("MBR", "FAT32"), ("mbr", "fat32")):
            with self.subTest(scheme=scheme, filesystem=filesystem):
                partition = build_partition(partition_scheme=scheme, filesystem=filesystem)
                result = evaluate_default(build_inspection(partitions=(partition,)))
                self.assertTrue(result.passed)

    def test_sd_and_mmc_buses_pass(self) -> None:
        for bus_type in ("USB", "sd", "MMC", "Secure Digital"):
            with self.subTest(bus_type=bus_type):
                inspection = build_inspection(drive=build_drive(bus_type=bus_type))
                self.assertTrue(evaluate_default(inspection).passed)


class SafetyRefusalTests(unittest.TestCase):
    """Safety refusals: removable class, system and boot volumes, active partitions."""

    def test_safety_class_holds_exactly_four_codes(self) -> None:
        self.assertEqual(
            SAFETY_REFUSAL_CODES,
            frozenset(
                {
                    RefusalCode.NOT_REMOVABLE,
                    RefusalCode.SYSTEM_VOLUME,
                    RefusalCode.SYSTEM_STATE_UNKNOWN,
                    RefusalCode.ACTIVE_PARTITION,
                }
            ),
        )

    def test_fixed_flag_on_usb_bus_is_refused(self) -> None:
        inspection = build_inspection(drive=build_drive(is_removable=False))
        self.assertEqual(refusal_codes(evaluate_default(inspection)), (RefusalCode.NOT_REMOVABLE,))

    def test_removable_flag_on_other_bus_is_refused(self) -> None:
        for bus_type in ("sata", "nvme", None):
            with self.subTest(bus_type=bus_type):
                inspection = build_inspection(drive=build_drive(bus_type=bus_type))
                codes = refusal_codes(evaluate_default(inspection))
                self.assertEqual(codes, (RefusalCode.NOT_REMOVABLE,))

    def test_system_or_boot_volume_is_refused(self) -> None:
        inspection = build_inspection(drive=build_drive(is_system=True))
        self.assertEqual(refusal_codes(evaluate_default(inspection)), (RefusalCode.SYSTEM_VOLUME,))

    def test_unknown_system_state_is_refused(self) -> None:
        inspection = build_inspection(drive=build_drive(is_system=None))
        codes = refusal_codes(evaluate_default(inspection))
        self.assertEqual(codes, (RefusalCode.SYSTEM_STATE_UNKNOWN,))

    def test_active_partition_is_refused(self) -> None:
        inspection = build_inspection(partitions=(build_partition(is_active=True),))
        codes = refusal_codes(evaluate_default(inspection))
        self.assertEqual(codes, (RefusalCode.ACTIVE_PARTITION,))

    def test_safety_refusal_hides_every_other_refusal(self) -> None:
        partition = build_partition(filesystem="exfat", is_read_only=True)
        inspection = build_inspection(
            drive=build_drive(is_system=True), partitions=(partition,), available_bytes=0
        )
        self.assertEqual(refusal_codes(evaluate_default(inspection)), (RefusalCode.SYSTEM_VOLUME,))

    def test_several_safety_refusals_return_together(self) -> None:
        inspection = build_inspection(
            drive=build_drive(is_removable=False, is_system=True),
            partitions=(build_partition(is_active=True),),
        )
        self.assertEqual(
            refusal_codes(evaluate_default(inspection)),
            (
                RefusalCode.NOT_REMOVABLE,
                RefusalCode.SYSTEM_VOLUME,
                RefusalCode.ACTIVE_PARTITION,
            ),
        )


class MediaAndLayoutRefusalTests(unittest.TestCase):
    """Refusals for read-only media, filesystem state, and partition layout."""

    def assert_single_refusal(self, inspection: DriveInspection, code: RefusalCode) -> None:
        """Assert that the inspection yields exactly one refusal with the code."""
        self.assertEqual(refusal_codes(evaluate_default(inspection)), (code,))

    def test_read_only_drive_is_refused(self) -> None:
        inspection = build_inspection(drive=build_drive(is_read_only=True))
        self.assert_single_refusal(inspection, RefusalCode.READ_ONLY_MEDIA)

    def test_read_only_partition_is_refused(self) -> None:
        inspection = build_inspection(partitions=(build_partition(is_read_only=True),))
        self.assert_single_refusal(inspection, RefusalCode.READ_ONLY_MEDIA)

    def test_filesystem_errors_are_refused_with_repair_guidance(self) -> None:
        partition = build_partition(filesystem_health=FilesystemHealth.ERRORS)
        result = evaluate_default(build_inspection(partitions=(partition,)))
        self.assertEqual(refusal_codes(result), (RefusalCode.FILESYSTEM_ERRORS,))
        self.assertIn("repair", result.refusals[0].guidance.casefold())

    def test_unknown_partition_layout_is_refused(self) -> None:
        inspection = build_inspection(partitions=(build_partition(partition_scheme=None),))
        self.assert_single_refusal(inspection, RefusalCode.UNKNOWN_PARTITION_LAYOUT)

    def test_gpt_scheme_is_refused(self) -> None:
        inspection = build_inspection(partitions=(build_partition(partition_scheme="gpt"),))
        self.assert_single_refusal(inspection, RefusalCode.UNSUPPORTED_PARTITION_SCHEME)

    def test_drive_without_partitions_is_refused(self) -> None:
        self.assert_single_refusal(build_inspection(partitions=()), RefusalCode.PARTITION_COUNT)

    def test_drive_with_two_partitions_is_refused(self) -> None:
        second = build_partition(name="sdb2", path="/dev/sdb2", partition_number=2, mount_points=())
        inspection = build_inspection(partitions=(build_partition(), second))
        self.assert_single_refusal(inspection, RefusalCode.PARTITION_COUNT)

    def test_logical_partition_is_refused(self) -> None:
        inspection = build_inspection(partitions=(build_partition(is_primary=False),))
        self.assert_single_refusal(inspection, RefusalCode.NOT_PRIMARY_PARTITION)

    def test_unknown_filesystem_is_refused(self) -> None:
        inspection = build_inspection(partitions=(build_partition(filesystem=None),))
        self.assert_single_refusal(inspection, RefusalCode.UNKNOWN_FILESYSTEM)

    def test_exfat_is_refused(self) -> None:
        inspection = build_inspection(partitions=(build_partition(filesystem="exfat"),))
        self.assert_single_refusal(inspection, RefusalCode.UNSUPPORTED_FILESYSTEM)

    def test_unmounted_partition_is_refused(self) -> None:
        inspection = build_inspection(partitions=(build_partition(mount_points=()),))
        self.assert_single_refusal(inspection, RefusalCode.NOT_MOUNTED)

    def test_missing_space_record_is_refused(self) -> None:
        self.assert_single_refusal(
            build_inspection(available_bytes=None), RefusalCode.SPACE_UNKNOWN
        )

    def test_every_non_safety_refusal_is_listed(self) -> None:
        partition = build_partition(
            filesystem="exfat", is_read_only=True, filesystem_health=FilesystemHealth.ERRORS
        )
        inspection = build_inspection(partitions=(partition,), available_bytes=0)
        self.assertEqual(
            refusal_codes(evaluate_default(inspection)),
            (
                RefusalCode.READ_ONLY_MEDIA,
                RefusalCode.FILESYSTEM_ERRORS,
                RefusalCode.UNSUPPORTED_FILESYSTEM,
                RefusalCode.INSUFFICIENT_SPACE,
            ),
        )


class UnverifiedFactTests(unittest.TestCase):
    """Facts without a source pass and appear as not verified."""

    def test_unknown_facts_pass_and_are_listed(self) -> None:
        partition = build_partition(is_active=None, is_primary=None, filesystem_health=None)
        result = evaluate_default(build_inspection(partitions=(partition,)))
        self.assertTrue(result.passed)
        self.assertEqual(
            result.unverified_facts,
            (FACT_ACTIVE_PARTITION, FACT_FILESYSTEM_HEALTH, FACT_PRIMARY_PARTITION),
        )

    def test_inferred_facts_are_listed(self) -> None:
        inspection = build_inspection(inferred_facts=frozenset({FACT_FILESYSTEM_HEALTH}))
        result = evaluate_default(inspection)
        self.assertTrue(result.passed)
        self.assertEqual(result.unverified_facts, (FACT_FILESYSTEM_HEALTH,))


class RequestRefusalTests(unittest.TestCase):
    """Refusals that depend on the export request."""

    def test_unknown_database_format_is_refused(self) -> None:
        for supported in (None, False):
            with self.subTest(supported=supported):
                profile = DjProfileRequest("Unknown player", database_format_supported=supported)
                request = build_request(profiles=(profile,))
                result = evaluate_preflight(build_inspection(), request)
                self.assertEqual(refusal_codes(result), (RefusalCode.UNKNOWN_DATABASE_FORMAT,))
                self.assertIn("Unknown player", result.refusals[0].message)

    def test_blocked_file_size_is_exactly_4_gib(self) -> None:
        self.assertEqual(FAT32_BLOCKED_FILE_BYTES, 4 * 1024**3)

    def test_largest_fat32_file_passes(self) -> None:
        request = build_request(file_sizes=(FAT32_BLOCKED_FILE_BYTES - 1,))
        result = evaluate_preflight(build_inspection(), request)
        self.assertTrue(result.passed)

    def test_one_4_gib_file_blocks_the_whole_export(self) -> None:
        request = build_request(file_sizes=(1, FAT32_BLOCKED_FILE_BYTES, 2))
        result = evaluate_preflight(build_inspection(), request)
        self.assertEqual(refusal_codes(result), (RefusalCode.FILE_TOO_LARGE,))
        self.assertFalse(result.passed)

    def test_negative_file_size_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_preflight(build_inspection(), build_request(file_sizes=(-1,)))


class SpaceRefusalTests(unittest.TestCase):
    """Too little space blocks the export under both reserve kinds."""

    def setUp(self) -> None:
        self.request: PreflightRequest = build_request()
        self.estimate = estimate_export_bytes(
            self.request.file_sizes,
            self.request.directory_count,
            self.request.database_allowance_bytes,
        )

    def evaluate_with(self, available_bytes: int, reserve: ReservePolicy) -> PreflightResult:
        """Evaluate the passing stick with the given free space and reserve."""
        inspection = build_inspection(available_bytes=available_bytes)
        return evaluate_preflight(inspection, self.request, reserve)

    def test_exactly_enough_space_passes_without_reserve(self) -> None:
        self.assertTrue(self.evaluate_with(self.estimate, ReservePolicy()).passed)

    def test_one_byte_short_is_refused_without_reserve(self) -> None:
        result = self.evaluate_with(self.estimate - 1, ReservePolicy())
        self.assertEqual(refusal_codes(result), (RefusalCode.INSUFFICIENT_SPACE,))

    def test_percentage_reserve_blocks_and_passes_at_the_boundary(self) -> None:
        reserve = ReservePolicy(percent=10.0)
        needed = self.estimate + compute_reserve_bytes(self.estimate, reserve)
        self.assertGreater(needed, self.estimate)
        blocked = self.evaluate_with(needed - 1, reserve)
        self.assertEqual(refusal_codes(blocked), (RefusalCode.INSUFFICIENT_SPACE,))
        passing = self.evaluate_with(needed, reserve)
        self.assertTrue(passing.passed)
        self.assertEqual(passing.reserve_bytes, needed - self.estimate)

    def test_fixed_byte_reserve_blocks_and_passes_at_the_boundary(self) -> None:
        reserve = ReservePolicy(fixed_bytes=50_000_000)
        needed = self.estimate + 50_000_000
        blocked = self.evaluate_with(needed - 1, reserve)
        self.assertEqual(refusal_codes(blocked), (RefusalCode.INSUFFICIENT_SPACE,))
        self.assertTrue(self.evaluate_with(needed, reserve).passed)

    def test_larger_reserve_kind_decides(self) -> None:
        reserve = ReservePolicy(percent=10.0, fixed_bytes=1)
        needed = self.estimate + compute_reserve_bytes(self.estimate, ReservePolicy(percent=10.0))
        blocked = self.evaluate_with(needed - 1, reserve)
        self.assertEqual(refusal_codes(blocked), (RefusalCode.INSUFFICIENT_SPACE,))


if __name__ == "__main__":
    unittest.main()
