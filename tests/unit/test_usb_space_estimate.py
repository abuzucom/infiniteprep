"""Unit tests for the USB export space estimate and reserve (D96 to D99, D180, D199)."""

from __future__ import annotations

import unittest

from infiniteprep.services.usb.space import (
    WORST_CASE_CLUSTER_BYTES,
    ReservePolicy,
    compute_reserve_bytes,
    estimate_export_bytes,
)

CLUSTER = WORST_CASE_CLUSTER_BYTES


class WorstCaseClusterTests(unittest.TestCase):
    """The estimate assumes the largest FAT32 cluster for every volume."""

    def test_cluster_constant_is_64_kib(self) -> None:
        self.assertEqual(CLUSTER, 64 * 1024)


class EstimateExportBytesTests(unittest.TestCase):
    """The estimate rounds up and never undercounts."""

    def test_empty_plan_needs_no_space(self) -> None:
        self.assertEqual(estimate_export_bytes(()), 0)

    def test_one_byte_file_takes_data_and_directory_clusters(self) -> None:
        self.assertEqual(estimate_export_bytes((1,)), 2 * CLUSTER)

    def test_exact_cluster_file_is_not_rounded_further(self) -> None:
        self.assertEqual(estimate_export_bytes((CLUSTER,)), 2 * CLUSTER)

    def test_file_one_byte_over_a_cluster_takes_two_data_clusters(self) -> None:
        self.assertEqual(estimate_export_bytes((CLUSTER + 1,)), 3 * CLUSTER)

    def test_empty_file_still_takes_a_directory_cluster(self) -> None:
        self.assertEqual(estimate_export_bytes((0,)), CLUSTER)

    def test_each_directory_adds_one_cluster(self) -> None:
        self.assertEqual(estimate_export_bytes((), directory_count=3), 3 * CLUSTER)

    def test_database_allowance_is_added_unrounded(self) -> None:
        self.assertEqual(estimate_export_bytes((), database_allowance_bytes=12345), 12345)

    def test_all_parts_sum(self) -> None:
        estimate = estimate_export_bytes(
            (1, CLUSTER + 1), directory_count=2, database_allowance_bytes=10
        )
        self.assertEqual(estimate, (1 + 2) * CLUSTER + 2 * CLUSTER + 2 * CLUSTER + 10)

    def test_estimate_is_never_below_the_raw_sum(self) -> None:
        sizes = (0, 1, 4095, CLUSTER - 1, CLUSTER, 5 * CLUSTER + 7)
        self.assertGreaterEqual(estimate_export_bytes(sizes), sum(sizes))

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
        self.assertEqual(compute_reserve_bytes(10 * CLUSTER, ReservePolicy()), 0)

    def test_percentage_reserve_applies_to_the_estimate(self) -> None:
        self.assertEqual(compute_reserve_bytes(1000, ReservePolicy(percent=10.0)), 100)

    def test_percentage_reserve_rounds_up(self) -> None:
        self.assertEqual(compute_reserve_bytes(1001, ReservePolicy(percent=10.0)), 101)

    def test_fixed_byte_reserve_applies_alone(self) -> None:
        self.assertEqual(compute_reserve_bytes(1000, ReservePolicy(fixed_bytes=4096)), 4096)

    def test_larger_percentage_wins(self) -> None:
        reserve = ReservePolicy(percent=50.0, fixed_bytes=100)
        self.assertEqual(compute_reserve_bytes(1000, reserve), 500)

    def test_larger_fixed_bytes_win(self) -> None:
        reserve = ReservePolicy(percent=5.0, fixed_bytes=100)
        self.assertEqual(compute_reserve_bytes(1000, reserve), 100)

    def test_invalid_reserve_values_are_rejected(self) -> None:
        for reserve in (
            ReservePolicy(percent=-1.0),
            ReservePolicy(fixed_bytes=-1),
            ReservePolicy(percent=float("nan")),
            ReservePolicy(percent=float("inf")),
        ):
            with self.subTest(reserve=reserve), self.assertRaises(ValueError):
                compute_reserve_bytes(1000, reserve)

    def test_negative_estimate_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            compute_reserve_bytes(-1, ReservePolicy())


if __name__ == "__main__":
    unittest.main()
