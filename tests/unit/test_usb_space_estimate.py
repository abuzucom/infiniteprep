"""Unit tests for the USB export space estimate and reserve (D96 to D99, D180, D199)."""

from __future__ import annotations

import unittest

from infiniteprep.services.usb.space import (
    UPPER_BOUND_CLUSTER_BYTES,
    ReservePolicy,
    compute_reserve_bytes,
    estimate_export_bytes,
)

CLUSTER = UPPER_BOUND_CLUSTER_BYTES
EXPECTED_CLUSTER_BYTES = 64 * 1024

# One small file takes one data cluster and one directory cluster.
SMALL_FILE_CLUSTERS = 2
# A file one byte over a cluster takes two data clusters and one directory cluster.
SPILLED_FILE_CLUSTERS = 3
DIRECTORY_COUNT = 3
DATABASE_ALLOWANCE_BYTES = 12345

# Mixed plan: a one-byte file and a file one byte over a cluster.
MIXED_FILE_SIZES = (1, CLUSTER + 1)
MIXED_DATA_CLUSTERS = 3
MIXED_FILE_COUNT = 2
MIXED_DIRECTORY_COUNT = 2
MIXED_ALLOWANCE_BYTES = 10

BOUNDARY_FILE_SIZES = (0, 1, 4095, CLUSTER - 1, CLUSTER, 5 * CLUSTER + 7)

ESTIMATE_BYTES = 1000
UNEVEN_ESTIMATE_BYTES = 1001
FIVE_PERCENT = 5.0
TEN_PERCENT = 10.0
FIFTY_PERCENT = 50.0
TEN_PERCENT_OF_ESTIMATE = 100
TEN_PERCENT_OF_UNEVEN_ESTIMATE = 101
FIFTY_PERCENT_OF_ESTIMATE = 500
FIXED_RESERVE_BYTES = 4096
SMALL_FIXED_RESERVE_BYTES = 100


class UpperBoundClusterTests(unittest.TestCase):
    """The estimate assumes the largest FAT32 cluster for every volume."""

    def test_cluster_constant_is_64_kib(self) -> None:
        self.assertEqual(CLUSTER, EXPECTED_CLUSTER_BYTES)


class EstimateExportBytesTests(unittest.TestCase):
    """The estimate rounds up and never undercounts."""

    def test_empty_plan_needs_no_space(self) -> None:
        self.assertEqual(estimate_export_bytes(()), 0)

    def test_one_byte_file_takes_data_and_directory_clusters(self) -> None:
        self.assertEqual(estimate_export_bytes((1,)), SMALL_FILE_CLUSTERS * CLUSTER)

    def test_exact_cluster_file_is_not_rounded_further(self) -> None:
        self.assertEqual(estimate_export_bytes((CLUSTER,)), SMALL_FILE_CLUSTERS * CLUSTER)

    def test_file_one_byte_over_a_cluster_takes_two_data_clusters(self) -> None:
        self.assertEqual(estimate_export_bytes((CLUSTER + 1,)), SPILLED_FILE_CLUSTERS * CLUSTER)

    def test_empty_file_still_takes_a_directory_cluster(self) -> None:
        self.assertEqual(estimate_export_bytes((0,)), CLUSTER)

    def test_each_directory_adds_one_cluster(self) -> None:
        estimate = estimate_export_bytes((), directory_count=DIRECTORY_COUNT)
        self.assertEqual(estimate, DIRECTORY_COUNT * CLUSTER)

    def test_database_allowance_is_added_unrounded(self) -> None:
        estimate = estimate_export_bytes((), database_allowance_bytes=DATABASE_ALLOWANCE_BYTES)
        self.assertEqual(estimate, DATABASE_ALLOWANCE_BYTES)

    def test_all_parts_sum(self) -> None:
        estimate = estimate_export_bytes(
            MIXED_FILE_SIZES,
            directory_count=MIXED_DIRECTORY_COUNT,
            database_allowance_bytes=MIXED_ALLOWANCE_BYTES,
        )
        cluster_count = MIXED_DATA_CLUSTERS + MIXED_FILE_COUNT + MIXED_DIRECTORY_COUNT
        self.assertEqual(estimate, cluster_count * CLUSTER + MIXED_ALLOWANCE_BYTES)

    def test_estimate_is_never_below_the_raw_sum(self) -> None:
        estimate = estimate_export_bytes(BOUNDARY_FILE_SIZES)
        self.assertGreaterEqual(estimate, sum(BOUNDARY_FILE_SIZES))

    def test_negative_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            estimate_export_bytes((-1,))
        with self.assertRaises(ValueError):
            estimate_export_bytes((), directory_count=-1)
        with self.assertRaises(ValueError):
            estimate_export_bytes((), database_allowance_bytes=-1)


class ComputeReserveBytesTests(unittest.TestCase):
    """The reserve is the larger of the percentage and the fixed byte count."""

    def test_initial_reserve_is_zero(self) -> None:
        self.assertEqual(ReservePolicy(), ReservePolicy(percent=0.0, fixed_bytes=0))
        self.assertEqual(compute_reserve_bytes(ESTIMATE_BYTES, ReservePolicy()), 0)

    def test_percentage_reserve_applies_to_the_estimate(self) -> None:
        reserve = ReservePolicy(percent=TEN_PERCENT)
        self.assertEqual(compute_reserve_bytes(ESTIMATE_BYTES, reserve), TEN_PERCENT_OF_ESTIMATE)

    def test_percentage_reserve_rounds_up(self) -> None:
        reserve = ReservePolicy(percent=TEN_PERCENT)
        reserve_bytes = compute_reserve_bytes(UNEVEN_ESTIMATE_BYTES, reserve)
        self.assertEqual(reserve_bytes, TEN_PERCENT_OF_UNEVEN_ESTIMATE)

    def test_fixed_byte_reserve_applies_alone(self) -> None:
        reserve = ReservePolicy(fixed_bytes=FIXED_RESERVE_BYTES)
        self.assertEqual(compute_reserve_bytes(ESTIMATE_BYTES, reserve), FIXED_RESERVE_BYTES)

    def test_larger_percentage_wins(self) -> None:
        reserve = ReservePolicy(percent=FIFTY_PERCENT, fixed_bytes=SMALL_FIXED_RESERVE_BYTES)
        self.assertEqual(compute_reserve_bytes(ESTIMATE_BYTES, reserve), FIFTY_PERCENT_OF_ESTIMATE)

    def test_larger_fixed_bytes_win(self) -> None:
        reserve = ReservePolicy(percent=FIVE_PERCENT, fixed_bytes=SMALL_FIXED_RESERVE_BYTES)
        self.assertEqual(compute_reserve_bytes(ESTIMATE_BYTES, reserve), SMALL_FIXED_RESERVE_BYTES)

    def test_invalid_reserve_values_are_rejected(self) -> None:
        for reserve in (
            ReservePolicy(percent=-1.0),
            ReservePolicy(fixed_bytes=-1),
            ReservePolicy(percent=float("nan")),
            ReservePolicy(percent=float("inf")),
        ):
            with self.subTest(reserve=reserve), self.assertRaises(ValueError):
                compute_reserve_bytes(ESTIMATE_BYTES, reserve)

    def test_negative_estimate_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            compute_reserve_bytes(-1, ReservePolicy())


if __name__ == "__main__":
    unittest.main()
