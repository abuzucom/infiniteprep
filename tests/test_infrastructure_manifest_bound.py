#!/usr/bin/env python3
"""Cover the size bound on infrastructure manifest inspection.

The infrastructure gate reads a bounded prefix of a YAML or JSON file to
decide whether it holds a Kubernetes manifest. The gate skips any file above
the bound. These tests reach that branch with a synthetic file, so hook
coverage does not depend on any large file in the repository.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import _gate_core

MANIFEST_TEXT = '{"apiVersion": "v1", "kind": "Pod"}\n'


class ManifestBoundTest(unittest.TestCase):
    """A manifest is classified from its content only below the size bound."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "resource.json"

    def test_a_small_manifest_is_protected(self):
        self.path.write_text(MANIFEST_TEXT, encoding="utf-8")
        self.assertTrue(
            _gate_core.is_protected_infrastructure_path(str(self.path)))

    def test_oversize_manifest_skipped(self):
        padding = " " * _gate_core.MAX_INFRASTRUCTURE_FILE_BYTES
        self.path.write_text(MANIFEST_TEXT + padding, encoding="utf-8")
        self.assertGreater(
            self.path.stat().st_size, _gate_core.MAX_INFRASTRUCTURE_FILE_BYTES)
        self.assertFalse(
            _gate_core.is_protected_infrastructure_path(str(self.path)))

    def test_missing_file_unprotected(self):
        self.assertFalse(
            _gate_core.is_protected_infrastructure_path(str(self.path)))


if __name__ == "__main__":
    unittest.main()
