#!/usr/bin/env python3
"""Test that the compliance tree exemption manifest is a protected gate path."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_compliance_tree
import check_gate_pr_integrity


class GateExemptionProtectionTest(unittest.TestCase):
    """A pull request that edits the manifest needs gate change approval."""

    def test_manifest_is_protected_file(self) -> None:
        self.assertIn(check_compliance_tree.EXEMPTIONS_FILE,
                      check_gate_pr_integrity.PROTECTED_FILES)

    def test_manifest_case_variant_protected(self) -> None:
        self.assertIn(check_compliance_tree.EXEMPTIONS_FILE.upper().casefold(),
                      check_gate_pr_integrity.FOLDED_FILES)


if __name__ == "__main__":
    unittest.main()
