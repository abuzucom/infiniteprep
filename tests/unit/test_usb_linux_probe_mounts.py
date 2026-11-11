"""Unit tests for mount table decoding in the Linux USB preflight probe (D138)."""

from __future__ import annotations

import json
import subprocess
import unittest

from infiniteprep.services.usb.linux_probe import LinuxPreflightProbe
from infiniteprep.services.usb.models import FilesystemHealth
from tests.unit.usb_preflight_support import build_inspection, build_partition

SPACED_MOUNT_PATH = "/media/dj/MY STICK"
ESCAPED_MOUNT_PATH = "/media/dj/MY\\040STICK"
LSBLK_OUTPUT = json.dumps(
    {
        "blockdevices": [
            {
                "name": "sdb",
                "type": "disk",
                "mountpoints": [None],
                "children": [
                    {
                        "name": "sdb1",
                        "type": "part",
                        "partn": 1,
                        "partflags": None,
                        "parttype": "0xc",
                        "mountpoints": [SPACED_MOUNT_PATH],
                    }
                ],
            }
        ]
    }
)


def run_fixed_commands(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Replay lsblk output and an empty udevadm reply."""
    output = LSBLK_OUTPUT if cmd[0] == "lsblk" else ""
    return subprocess.CompletedProcess(cmd, 0, output, "")


def probe_health(mount_options: str) -> FilesystemHealth | None:
    """Probe a stick mounted at a path with a space and return its health."""
    mount_table = f"/dev/sdb1 {ESCAPED_MOUNT_PATH} vfat {mount_options} 0 0\n"
    partition = build_partition(mount_points=(SPACED_MOUNT_PATH,), filesystem_health=None)
    probe = LinuxPreflightProbe(run_fixed_commands, lambda: mount_table)
    return probe.probe(build_inspection(partitions=(partition,))).partitions[0].filesystem_health


class EscapedMountPathTests(unittest.TestCase):
    """The kernel writes a space in a mount path as an octal escape."""

    def test_writable_mount_with_a_space_is_healthy(self) -> None:
        self.assertIs(probe_health("rw,relatime"), FilesystemHealth.HEALTHY)

    def test_read_only_mount_with_a_space_is_an_error(self) -> None:
        self.assertIs(probe_health("ro,relatime"), FilesystemHealth.ERRORS)

    def test_short_mount_table_line_is_skipped(self) -> None:
        partition = build_partition(mount_points=(SPACED_MOUNT_PATH,), filesystem_health=None)
        probe = LinuxPreflightProbe(run_fixed_commands, lambda: "/dev/sdb1 incomplete\n")
        result = probe.probe(build_inspection(partitions=(partition,)))
        self.assertIsNone(result.partitions[0].filesystem_health)


if __name__ == "__main__":
    unittest.main()
