"""Unit tests for the writability check of the pre-write USB recheck (D132)."""

from __future__ import annotations

import unittest

from infiniteprep.services.usb.recheck import (
    AbortCode,
    capture_target_snapshot,
    recheck_target,
)
from tests.unit.usb_preflight_support import (
    MOUNT_PATH,
    build_inspection,
    build_partition,
    build_request,
)

OTHER_MOUNT_PATH = "/media/dj/STICK1"


class RecordingWritabilityCheck:
    """Fake writability check that records each path and returns a fixed answer."""

    def __init__(self, writable: bool) -> None:
        self.writable = writable
        self.paths: list[str] = []

    def __call__(self, path: str) -> bool:
        self.paths.append(path)
        return self.writable


class FreshPathWritabilityTests(unittest.TestCase):
    """The recheck reads writability from the fresh mount path on every call."""

    def recheck_with_mounts(
        self, mount_points: tuple[str, ...], check: RecordingWritabilityCheck
    ) -> tuple[AbortCode, ...]:
        """Recheck a fresh inspection with the given mount points and return abort codes."""
        snapshot = capture_target_snapshot(build_inspection())
        fresh = build_inspection(partitions=(build_partition(mount_points=mount_points),))
        result = recheck_target(snapshot, fresh, build_request(), is_path_writable=check)
        return tuple(reason.code for reason in result.abort_reasons)

    def test_unchanged_path_is_checked_once(self) -> None:
        check = RecordingWritabilityCheck(writable=True)
        self.assertEqual(self.recheck_with_mounts((MOUNT_PATH,), check), ())
        self.assertEqual(check.paths, [MOUNT_PATH])

    def test_changed_unwritable_path_reports_both_reasons(self) -> None:
        check = RecordingWritabilityCheck(writable=False)
        codes = self.recheck_with_mounts((OTHER_MOUNT_PATH,), check)
        self.assertEqual(codes[:2], (AbortCode.MOUNT_PATH_CHANGED, AbortCode.NOT_WRITABLE))
        self.assertEqual(check.paths, [OTHER_MOUNT_PATH])

    def test_changed_writable_path_reports_no_writability_reason(self) -> None:
        check = RecordingWritabilityCheck(writable=True)
        codes = self.recheck_with_mounts((OTHER_MOUNT_PATH,), check)
        self.assertIn(AbortCode.MOUNT_PATH_CHANGED, codes)
        self.assertNotIn(AbortCode.NOT_WRITABLE, codes)

    def test_unmounted_target_is_not_writable_without_a_path_check(self) -> None:
        check = RecordingWritabilityCheck(writable=True)
        codes = self.recheck_with_mounts((), check)
        self.assertEqual(codes[:2], (AbortCode.MOUNT_PATH_CHANGED, AbortCode.NOT_WRITABLE))
        self.assertEqual(check.paths, [])


if __name__ == "__main__":
    unittest.main()
