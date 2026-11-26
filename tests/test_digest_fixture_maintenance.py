#!/usr/bin/env python3
"""Cover the Git maintenance settings of the digest backfill fixture.

A commit in the fixture template can start detached Git maintenance. That
process removes .git/objects/maintenance.lock while copy_into runs
shutil.copytree, and the copy fails with a missing file. The fixture turns
automatic maintenance and automatic garbage collection off to close the race.
"""
import subprocess
import unittest
from pathlib import Path

from tests.test_sync_digest_index import BackfillHistory


def read_local_config(repository: Path, key: str) -> str:
    """Return one value from the repository-local Git configuration."""
    result = subprocess.run(
        ["git", "-C", str(repository), "config", "--local", "--get", key],
        capture_output=True, text=True, check=False)
    return result.stdout.strip()


class DigestFixtureMaintenanceTest(unittest.TestCase):
    """The backfill template never starts background Git maintenance."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.history = BackfillHistory(cls.addClassCleanup)

    def test_template_disables_automatic_maintenance(self):
        self.assertEqual(
            read_local_config(self.history.template, "maintenance.auto"),
            "false")

    def test_template_disables_automatic_garbage_collection(self):
        self.assertEqual(
            read_local_config(self.history.template, "gc.auto"), "0")


if __name__ == "__main__":
    unittest.main()
