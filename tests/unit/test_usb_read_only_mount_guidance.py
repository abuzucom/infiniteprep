"""Unit tests for the refusal guidance of a read-only mount on writable media (D137, D138)."""

from __future__ import annotations

import json
import subprocess
import unittest

from infiniteprep.services.usb.linux_probe import LinuxPreflightProbe
from infiniteprep.services.usb.preflight import RefusalCode, evaluate_preflight
from tests.unit.usb_preflight_support import (
    MOUNT_PATH,
    build_inspection,
    build_partition,
    build_request,
)

READ_ONLY_MOUNT_TABLE = f"/dev/sdb1 {MOUNT_PATH} vfat ro,relatime 0 0\n"
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
                        "mountpoints": [MOUNT_PATH],
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


class ReadOnlyMountGuidanceTests(unittest.TestCase):
    """A read-only mount on writable media refuses and names both causes."""

    def test_refusal_guidance_names_a_deliberate_read_only_mount(self) -> None:
        partition = build_partition(filesystem_health=None)
        probe = LinuxPreflightProbe(run_fixed_commands, lambda: READ_ONLY_MOUNT_TABLE)
        probed = probe.probe(build_inspection(partitions=(partition,)))
        result = evaluate_preflight(probed, build_request())
        self.assertEqual(
            [refusal.code for refusal in result.refusals], [RefusalCode.FILESYSTEM_ERRORS]
        )
        guidance = result.refusals[0].guidance.casefold()
        self.assertIn("repair", guidance)
        self.assertIn("read-only on purpose", guidance)
        self.assertIn("never repairs or remounts", guidance)


if __name__ == "__main__":
    unittest.main()
