#!/usr/bin/env python3
"""The test runner and hook coverage share one shard discovery (S10)."""
import tempfile
import unittest
from pathlib import Path

from scripts import check_hook_coverage, run_tests

SYNTHETIC_MODULE = (
    "import unittest\n\n"
    "class Alpha(unittest.TestCase):\n"
    "    def test_one(self):\n        self.assertTrue(True)\n"
    "    def test_two(self):\n        self.assertTrue(True)\n\n"
    "class Beta(unittest.TestCase):\n"
    "    def test_three(self):\n        self.assertTrue(True)\n"
)


class ShardDiscoveryParityTest(unittest.TestCase):
    """Both callers see the same shards and the same test IDs."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_parity_sample.py").write_text(
            SYNTHETIC_MODULE, encoding="utf-8")

    def test_runner_uses_shared_shards(self):
        identifiers, shards = run_tests._discovered_tests(str(self.root))
        self.assertEqual(shards, check_hook_coverage.discover_test_shards(str(self.root)))
        self.assertEqual(sum(identifiers.values()), 3)
        self.assertEqual(len(shards), 2)

    def test_shared_flatten_helper(self):
        suite = unittest.TestSuite([unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])])
        flattened = list(check_hook_coverage.iterate_tests(suite))
        self.assertEqual(len(flattened), 1)
        self.assertEqual(run_tests._suite_ids(suite)[flattened[0].id()], 1)


if __name__ == "__main__":
    unittest.main()
