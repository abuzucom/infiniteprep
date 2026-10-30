"""Exercise generated adjective-surname-suffix branch name detection."""

import subprocess
import sys
import unittest
from pathlib import Path

from scripts import check_branch_name

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_branch_name.py"
TIMEOUT_SECONDS = 30
GENERATED_MESSAGE = "uses a generated adjective-surname-suffix name"


def run_checker(branch: str) -> subprocess.CompletedProcess:
    """Run the checker CLI in strict agent preflight mode."""
    return subprocess.run(
        [sys.executable, str(CHECKER_PATH), "--strict-agent-preflight", branch],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


class GeneratedBranchNameTest(unittest.TestCase):
    """Reject harness-generated names and keep descriptive names passing."""

    def test_rejects_generated_name_with_letter_suffix(self):
        result = run_checker("chore/dreamy-goldberg-nwjlsu")
        self.assertEqual(result.returncode, 1)
        self.assertIn(GENERATED_MESSAGE, result.stderr)

    def test_rejects_generated_name_with_mixed_suffix(self):
        violations = check_branch_name.find_violations("chore/zealous-gauss-zz1yiy", strict=True)
        self.assertTrue(any(GENERATED_MESSAGE in item for item in violations))
        self.assertTrue(any("opaque mixed alphanumeric" in item for item in violations))

    def test_accepts_descriptive_names(self):
        for branch in (
            "fix/branch-name-validation",
            "fix/happy-path-tests",
            "docs/fix-lengths-widths",
            "fix/branch-check-generated-names",
            "feat/clever-hopper-support",
        ):
            with self.subTest(branch=branch):
                result = run_checker(branch)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_requires_adjective_before_surname(self):
        self.assertEqual(
            check_branch_name.find_violations("chore/goldberg-dreamy-nwjlsu", strict=True),
            [],
        )


if __name__ == "__main__":
    unittest.main()
