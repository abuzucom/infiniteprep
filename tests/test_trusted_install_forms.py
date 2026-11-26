#!/usr/bin/env python3
"""The privileged trusted install accepts its current and hardened forms only."""
import copy
import unittest
from pathlib import Path

import yaml

from scripts import check_compliance_tree as checker

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "immutable-conflict-check.yml"
INSTALL_STEP = "Install trusted checker dependencies"
SETUP_STEP = "Set up Python"
HARDENED_COMMAND = checker.TRUSTED_REQUIREMENTS_COMMAND + " --only-binary :all:"


def variant(command: str, python_version: str) -> tuple:
    """Return the trusted workflow with one install command and Python version."""
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = copy.deepcopy(yaml.safe_load(text))
    for step in workflow["jobs"]["immutable-compliance"]["steps"]:
        if step.get("name") == INSTALL_STEP:
            step["run"] = command
        if step.get("name") == SETUP_STEP:
            step["with"] = {"python-version": python_version}
    return workflow, text


class TrustedInstallFormTest(unittest.TestCase):
    """The schema admits the wheel-only install so the job can adopt it."""

    def violations(self, command: str, python_version: str) -> list:
        workflow, text = variant(command, python_version)
        return checker._pull_target_violations(workflow, text, str(WORKFLOW))

    def test_unhardened_form_fails(self):
        self.assertTrue(self.violations(checker.TRUSTED_REQUIREMENTS_COMMAND, "3.x"))

    def test_hardened_form_passes(self):
        self.assertEqual(self.violations(HARDENED_COMMAND, "3.14"), [])

    def test_other_install_forms_fail(self):
        for command in (HARDENED_COMMAND + " --pre",
                        checker.TRUSTED_REQUIREMENTS_COMMAND + " --index-url https://example.invalid",
                        "python -m pip install PyYAML"):
            with self.subTest(command=command):
                self.assertTrue(self.violations(command, "3.14"))


if __name__ == "__main__":
    unittest.main()
