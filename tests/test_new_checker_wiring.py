#!/usr/bin/env python3
"""Pin the CI and pre-commit wiring of every checker added for skills and zones.

Each row names a checker, the command text its CI step runs, and the command
text its pre-commit hook runs. Removing a step or a hook fails this test, so a
blocking claim in the enforcement matrix cannot outlive its wiring.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "agents-compliance.yml"
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
WIRING = (
    ("check_forbidden_paths.py",
     "python scripts/check_forbidden_paths.py --base \"$PR_BASE_SHA\" --head \"$PR_HEAD_SHA\"",
     "python scripts/check_forbidden_paths.py --staged"),
    ("check_shared_neutrality.py",
     "python scripts/check_shared_neutrality.py",
     "python scripts/check_shared_neutrality.py"),
)


class NewCheckerWiringTest(unittest.TestCase):
    """Every new blocking checker runs in CI and in pre-commit."""

    def test_checkers_run_in_ci(self):
        text = CI_WORKFLOW.read_text(encoding="utf-8")
        for checker, ci_command, _hook_command in WIRING:
            with self.subTest(checker=checker):
                self.assertIn(ci_command, text)

    def test_checkers_run_in_pre_commit(self):
        text = PRE_COMMIT_CONFIG.read_text(encoding="utf-8")
        for checker, _ci_command, hook_command in WIRING:
            with self.subTest(checker=checker):
                self.assertIn(f"entry: {hook_command}", text)

    def test_wired_checkers_exist(self):
        for checker, _ci_command, _hook_command in WIRING:
            with self.subTest(checker=checker):
                self.assertTrue((ROOT / "scripts" / checker).is_file())


if __name__ == "__main__":
    unittest.main()
