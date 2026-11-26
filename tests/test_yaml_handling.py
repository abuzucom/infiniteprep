#!/usr/bin/env python3
"""YAML handling shared by the workflow and container checkers.

Line numbers must not depend on which checker imports first. Recursive YAML
aliases must not crash the action pin checker. The compliance tree keeps
rejecting duplicate workflow keys.
"""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
WORKFLOW = (
    "jobs:\n  build:\n    steps:\n      - name: co\n"
    "        uses: actions/checkout@v4\n"
)
EXPECTED_CHECKOUT_FINDING = (
    "w.yml:5: actions/checkout missing persist-credentials: false (Rule 11)"
)
IMPORT_ORDERS = (
    ("check_persist_credentials",),
    ("check_dockerfile_root", "check_persist_credentials"),
    ("check_persist_credentials", "check_dockerfile_root"),
)
PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
modules = {name: __import__(name) for name in json.loads(sys.argv[2])}
import yaml
print(json.dumps({
    "findings": modules["check_persist_credentials"].find_violations(
        sys.argv[3], "w.yml"),
    "plain_dict": type(yaml.safe_load("a: 1")) is dict,
}))
"""


def load_script(name: str):
    """Import one script module from its repository path."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def probe(order: tuple) -> dict:
    """Import checkers in `order` in a fresh interpreter and report results."""
    result = subprocess.run(
        [sys.executable, "-I", "-c", PROBE, str(SCRIPTS), json.dumps(order), WORKFLOW],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class ImportOrderTest(unittest.TestCase):
    """Checker line numbers and the global loader ignore import order."""

    def test_import_order_line_numbers(self):
        for order in IMPORT_ORDERS:
            with self.subTest(order=order):
                self.assertEqual(probe(order)["findings"], [EXPECTED_CHECKOUT_FINDING])

    def test_global_safe_loader_stays_unmodified(self):
        for order in IMPORT_ORDERS:
            with self.subTest(order=order):
                self.assertTrue(probe(order)["plain_dict"])


class ActionPinAliasTest(unittest.TestCase):
    """Recursive YAML aliases neither crash nor duplicate findings."""

    @classmethod
    def setUpClass(cls):
        cls.pins = load_script("check_action_pins")

    def test_mapping_cycle_without_uses(self):
        self.assertEqual(self.pins.find_violations("a: &x {b: *x}\n", "w.yml"), [])

    def test_sequence_cycle_without_uses(self):
        self.assertEqual(self.pins.find_violations("a: &x [*x]\n", "w.yml"), [])

    def test_cycle_unpinned_action_reported(self):
        findings = self.pins.find_violations(
            "a: &x {uses: actions/checkout@v4, b: *x}\n", "w.yml")
        self.assertEqual(findings, ["w.yml: a.uses must use a full commit SHA"])


class ComplianceDuplicateKeyTest(unittest.TestCase):
    """The compliance tree still rejects duplicate workflow keys."""

    @classmethod
    def setUpClass(cls):
        cls.tree = load_script("check_compliance_tree")
        cls.persist = load_script("check_persist_credentials")

    def test_duplicate_key_is_malformed(self):
        findings = self.tree._workflow_violations("a: 1\na: 2\n", "w.yml", self.persist)
        self.assertEqual(findings, ["w.yml: malformed workflow YAML cannot be checked"])


if __name__ == "__main__":
    unittest.main()
