#!/usr/bin/env python3
"""Cover the scope of the Helm values file name match.

The gate protects Kubernetes and Helm configuration. A Helm values file
carries a YAML extension. A source file named `values.ts` carries no
Kubernetes configuration, so the name match must not cover it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import _gate_core


class ValuesFileScopeTest(unittest.TestCase):
    """Only YAML values file names reach the protected set."""

    def test_plain_values_file_protected(self):
        self.assertTrue(_gate_core.is_protected_infrastructure_path("values.yaml"))

    def test_other_yaml_values_protected(self):
        self.assertTrue(
            _gate_core.is_protected_infrastructure_path("values-production.yaml"))

    def test_dotted_values_file_protected(self):
        self.assertTrue(
            _gate_core.is_protected_infrastructure_path("values.common.yaml"))

    def test_typescript_values_unprotected(self):
        self.assertFalse(_gate_core.is_protected_infrastructure_path("values.ts"))

    def test_typescript_values_test_unprotected(self):
        self.assertFalse(
            _gate_core.is_protected_infrastructure_path("values.test.ts"))

    def test_javascript_values_unprotected(self):
        self.assertFalse(_gate_core.is_protected_infrastructure_path("values.js"))


if __name__ == "__main__":
    unittest.main()
