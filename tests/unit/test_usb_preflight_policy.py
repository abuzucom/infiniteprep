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

KNOWN_FREE_BYTES = 10**9
SECOND_PARTITION_NUMBER = 2
EXPECTED_BLOCKED_FILE_BYTES = 4 * 1024**3
# One oversized file between two small files.
OVERSIZED_PLAN_FILE_SIZES = (1, FAT32_BLOCKED_FILE_BYTES, 2)
TEN_PERCENT = 10.0
FIXED_RESERVE_BYTES = 50_000_000


def refusal_codes(result: PreflightResult) -> tuple[RefusalCode, ...]:
    """Return the refusal codes of a result in order."""
    return tuple(refusal.code for refusal in result.refusals)


def evaluate_default(inspection: DriveInspection) -> PreflightResult:
    """Evaluate an inspection against the passing request with no reserve."""
    return evaluate_preflight(inspection, build_request())


class PassingTargetTests(unittest.TestCase):
    """A removable FAT32 stick with one primary MBR partition passes."""

    def test_passing_stick_has_no_refusals(self) -> None:
        """The reference stick passes with no refusal and no unverified fact."""
        result = evaluate_default(build_inspection())
        self.assertEqual(result.refusals, ())
        self.assertTrue(result.passed)
        self.assertEqual(result.unverified_facts, ())

    def test_result_reports_space_and_mount(self) -> None:
        """The result carries the estimate, the reserve, the free space, and the mount path."""
        request = build_request()
        inspection = build_inspection(available_bytes=KNOWN_FREE_BYTES)
        result = evaluate_preflight(inspection, request)
        expected = estimate_export_bytes(
            request.file_sizes, request.directory_count, request.database_allowance_bytes
        )
        self.assertEqual(result.estimate_bytes, expected)
        self.assertEqual(result.reserve_bytes, 0)
        self.assertEqual(result.available_bytes, KNOWN_FREE_BYTES)
        self.assertEqual(result.mount_path, MOUNT_PATH)

    def test_platform_scheme_fs_names_pass(self) -> None:
        """Each platform spelling of MBR and FAT32 passes."""
        for scheme, filesystem in (("dos", "vfat"), ("MBR", "FAT32"), ("mbr", "fat32")):
            with self.subTest(scheme=scheme, filesystem=filesystem):
                partition = build_partition(partition_scheme=scheme, filesystem=filesystem)
                result = evaluate_default(build_inspection(partitions=(partition,)))
                self.assertTrue(result.passed)

    def test_sd_and_mmc_buses_pass(self) -> None:
        """USB, SD, and MMC bus names pass."""
        for bus_type in ("USB", "sd", "MMC", "Secure Digital"):
            with self.subTest(bus_type=bus_type):
                inspection = build_inspection(drive=build_drive(bus_type=bus_type))
                self.assertTrue(evaluate_default(inspection).passed)


class SafetyRefusalTests(unittest.TestCase):
    """Safety refusals: removable class, system and boot volumes, active partitions."""

    def test_safety_class_holds_four_codes(self) -> None:
        """The safety class holds the bus, system, unknown system, and active codes."""
        self.assertEqual(
            SAFETY_REFUSAL_CODES,
            frozenset(
                {
                    RefusalCode.UNSUPPORTED_BUS,
                    RefusalCode.SYSTEM_VOLUME,
                    RefusalCode.SYSTEM_STATE_UNKNOWN,
                    RefusalCode.ACTIVE_PARTITION,
                }
            ),
        )

    def test_removable_flag_other_bus_refused(self) -> None:
        """Preflight refuses a removable drive on a SATA, NVMe, or unknown bus."""
        for bus_type in ("sata", "nvme", None):
            with self.subTest(bus_type=bus_type):
                inspection = build_inspection(drive=build_drive(bus_type=bus_type))
                codes = refusal_codes(evaluate_default(inspection))
                self.assertEqual(codes, (RefusalCode.UNSUPPORTED_BUS,))

    def test_system_or_boot_volume_refused(self) -> None:
        """Preflight refuses a system or boot drive."""
        inspection = build_inspection(drive=build_drive(is_system=True))
        self.assertEqual(refusal_codes(evaluate_default(inspection)), (RefusalCode.SYSTEM_VOLUME,))

    def test_unknown_system_state_is_refused(self) -> None:
        """Preflight refuses a drive with an unknown system state."""
        inspection = build_inspection(drive=build_drive(is_system=None))
        codes = refusal_codes(evaluate_default(inspection))
        self.assertEqual(codes, (RefusalCode.SYSTEM_STATE_UNKNOWN,))

    def test_active_partition_is_refused(self) -> None:
        """Preflight refuses an active partition."""
        inspection = build_inspection(partitions=(build_partition(is_active=True),))
        codes = refusal_codes(evaluate_default(inspection))
        self.assertEqual(codes, (RefusalCode.ACTIVE_PARTITION,))

    def test_safety_refusal_hides_other_refusals(self) -> None:
        """A safety refusal stands alone in the result despite other faults."""
        partition = build_partition(filesystem="exfat", is_read_only=True)
        inspection = build_inspection(
            drive=build_drive(is_system=True), partitions=(partition,), available_bytes=0
        )
        self.assertEqual(refusal_codes(evaluate_default(inspection)), (RefusalCode.SYSTEM_VOLUME,))


class MediaAndLayoutRefusalTests(unittest.TestCase):
    """Refusals for read-only media, filesystem state, and partition layout."""

    def assert_single_refusal(self, inspection: DriveInspection, code: RefusalCode) -> None:
        """Assert that the inspection yields exactly one refusal with the code."""
        self.assertEqual(refusal_codes(evaluate_default(inspection)), (code,))

    def test_read_only_drive_is_refused(self) -> None:
        """Preflight refuses a read-only drive as read-only media."""
        inspection = build_inspection(drive=build_drive(is_read_only=True))
        self.assert_single_refusal(inspection, RefusalCode.READ_ONLY_MEDIA)

    def test_read_only_partition_is_refused(self) -> None:
        """Preflight refuses a read-only partition as read-only media."""
        inspection = build_inspection(partitions=(build_partition(is_read_only=True),))
        self.assert_single_refusal(inspection, RefusalCode.READ_ONLY_MEDIA)

    def test_filesystem_errors_suggest_repair(self) -> None:
        """Preflight refuses filesystem errors with guidance that names a repair."""
        partition = build_partition(filesystem_health=FilesystemHealth.ERRORS)
        result = evaluate_default(build_inspection(partitions=(partition,)))
        self.assertEqual(refusal_codes(result), (RefusalCode.FILESYSTEM_ERRORS,))
        self.assertIn("repair", result.refusals[0].guidance.casefold())

    def test_unknown_partition_layout_is_refused(self) -> None:
        """Preflight refuses a partition with no known scheme as an unknown layout."""
        inspection = build_inspection(partitions=(build_partition(partition_scheme=None),))
        self.assert_single_refusal(inspection, RefusalCode.UNKNOWN_PARTITION_LAYOUT)

    def test_gpt_scheme_is_refused(self) -> None:
        """Preflight refuses a GPT partition scheme."""
        inspection = build_inspection(partitions=(build_partition(partition_scheme="gpt"),))
        self.assert_single_refusal(inspection, RefusalCode.UNSUPPORTED_PARTITION_SCHEME)

    def test_drive_without_partitions_is_refused(self) -> None:
        """Preflight refuses a drive with no partition on the partition count."""
        self.assert_single_refusal(build_inspection(partitions=()), RefusalCode.PARTITION_COUNT)

    def test_two_partition_drive_is_refused(self) -> None:
        """Preflight refuses a drive with two partitions on the partition count."""
        second = build_partition(
            name="sdb2",
            path="/dev/sdb2",
            partition_number=SECOND_PARTITION_NUMBER,
            mount_points=(),
        )
        inspection = build_inspection(partitions=(build_partition(), second))
        self.assert_single_refusal(inspection, RefusalCode.PARTITION_COUNT)

    def test_logical_partition_is_refused(self) -> None:
        """Preflight refuses a partition that is not primary."""
        inspection = build_inspection(partitions=(build_partition(is_primary=False),))
        self.assert_single_refusal(inspection, RefusalCode.NOT_PRIMARY_PARTITION)

    def test_unknown_filesystem_is_refused(self) -> None:
        """Preflight refuses a partition with no known filesystem."""
        inspection = build_inspection(partitions=(build_partition(filesystem=None),))
        self.assert_single_refusal(inspection, RefusalCode.UNKNOWN_FILESYSTEM)

    def test_exfat_is_refused(self) -> None:
        """Preflight refuses exFAT as an unsupported filesystem."""
        inspection = build_inspection(partitions=(build_partition(filesystem="exfat"),))
        self.assert_single_refusal(inspection, RefusalCode.UNSUPPORTED_FILESYSTEM)

    def test_unmounted_partition_is_refused(self) -> None:
        """Preflight refuses a partition with no mount point."""
        inspection = build_inspection(partitions=(build_partition(mount_points=()),))
        self.assert_single_refusal(inspection, RefusalCode.NOT_MOUNTED)

    def test_missing_space_record_is_refused(self) -> None:
        """Preflight refuses a target with no free space record."""
        self.assert_single_refusal(
            build_inspection(available_bytes=None), RefusalCode.SPACE_UNKNOWN
        )

    def test_every_non_safety_refusal_listed(self) -> None:
        """The result lists every non-safety refusal in a fixed order."""
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

    def test_unknown_facts_pass_and_listed(self) -> None:
        """Unknown active, health, and primary facts pass and appear as unverified."""
        partition = build_partition(is_active=None, is_primary=None, filesystem_health=None)
        result = evaluate_default(build_inspection(partitions=(partition,)))
        self.assertTrue(result.passed)
        self.assertEqual(
            result.unverified_facts,
            (FACT_ACTIVE_PARTITION, FACT_FILESYSTEM_HEALTH, FACT_PRIMARY_PARTITION),
        )

    def test_inferred_facts_are_listed(self) -> None:
        """An inferred fact passes and appears as unverified."""
        inspection = build_inspection(inferred_facts=frozenset({FACT_FILESYSTEM_HEALTH}))
        result = evaluate_default(inspection)
        self.assertTrue(result.passed)
        self.assertEqual(result.unverified_facts, (FACT_FILESYSTEM_HEALTH,))


class RequestRefusalTests(unittest.TestCase):
    """Refusals that depend on the export request."""

    def test_unknown_database_format_is_refused(self) -> None:
        """Preflight refuses a player profile without a supported format and names it."""
        for supported in (None, False):
            with self.subTest(supported=supported):
                profile = DjProfileRequest("Unknown player", database_format_supported=supported)
                request = build_request(profiles=(profile,))
                result = evaluate_preflight(build_inspection(), request)
                self.assertEqual(refusal_codes(result), (RefusalCode.UNKNOWN_DATABASE_FORMAT,))
                self.assertIn("Unknown player", result.refusals[0].message)

    def test_blocked_size_is_4_gib(self) -> None:
        """The FAT32 blocked file size equals 4 GiB."""
        self.assertEqual(FAT32_BLOCKED_FILE_BYTES, EXPECTED_BLOCKED_FILE_BYTES)

    def test_largest_fat32_file_passes(self) -> None:
        """A file one byte under 4 GiB passes."""
        request = build_request(file_sizes=(FAT32_BLOCKED_FILE_BYTES - 1,))
        result = evaluate_preflight(build_inspection(), request)
        self.assertTrue(result.passed)

    def test_4_gib_file_blocks_export(self) -> None:
        """One 4 GiB file in the plan blocks the whole export."""
        request = build_request(file_sizes=OVERSIZED_PLAN_FILE_SIZES)
        result = evaluate_preflight(build_inspection(), request)
        self.assertEqual(refusal_codes(result), (RefusalCode.FILE_TOO_LARGE,))
        self.assertFalse(result.passed)

    def test_negative_file_size_is_rejected(self) -> None:
        """A negative planned file size raises ValueError."""
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

    def test_exact_space_passes_without_reserve(self) -> None:
        """Free space equal to the estimate passes with no reserve."""
        self.assertTrue(self.evaluate_with(self.estimate, ReservePolicy()).passed)

    def test_byte_short_refused_without_reserve(self) -> None:
        """Preflight refuses free space one byte under the estimate."""
        result = self.evaluate_with(self.estimate - 1, ReservePolicy())
        self.assertEqual(refusal_codes(result), (RefusalCode.INSUFFICIENT_SPACE,))

    def test_percentage_reserve_boundary(self) -> None:
        """A percentage reserve blocks one byte under its total and passes at it."""
        reserve = ReservePolicy(percent=TEN_PERCENT)
        needed = self.estimate + compute_reserve_bytes(self.estimate, reserve)
        self.assertGreater(needed, self.estimate)
        blocked = self.evaluate_with(needed - 1, reserve)
        self.assertEqual(refusal_codes(blocked), (RefusalCode.INSUFFICIENT_SPACE,))
        passing = self.evaluate_with(needed, reserve)
        self.assertTrue(passing.passed)
        self.assertEqual(passing.reserve_bytes, needed - self.estimate)

    def test_fixed_byte_reserve_boundary(self) -> None:
        """A fixed byte reserve blocks one byte under its total and passes at it."""
        reserve = ReservePolicy(fixed_bytes=FIXED_RESERVE_BYTES)
        needed = self.estimate + FIXED_RESERVE_BYTES
        blocked = self.evaluate_with(needed - 1, reserve)
        self.assertEqual(refusal_codes(blocked), (RefusalCode.INSUFFICIENT_SPACE,))
        self.assertTrue(self.evaluate_with(needed, reserve).passed)

    def test_larger_reserve_kind_decides(self) -> None:
        """The larger reserve kind sets the total instead of the sum of both kinds."""
        reserve = ReservePolicy(percent=TEN_PERCENT, fixed_bytes=1)
        percent_only = ReservePolicy(percent=TEN_PERCENT)
        needed = self.estimate + compute_reserve_bytes(self.estimate, percent_only)
        blocked = self.evaluate_with(needed - 1, reserve)
        self.assertEqual(refusal_codes(blocked), (RefusalCode.INSUFFICIENT_SPACE,))
        # A summed reserve would need one byte more and block here. The larger kind passes.
        passing = self.evaluate_with(needed, reserve)
        self.assertTrue(passing.passed)
        self.assertEqual(passing.reserve_bytes, needed - self.estimate)


if __name__ == "__main__":
    unittest.main()
