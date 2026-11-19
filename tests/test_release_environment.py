"""Behavioral checks for the release approval environment in build-packages (D314, D315)."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github/workflows/build-packages.yml"
# The repository owner configures this environment with a required reviewer.
RELEASE_ENVIRONMENT = "release"
RELEASE_JOB = "release"


class ReleaseEnvironmentTest(unittest.TestCase):
    """The release job waits for owner approval. No other job uses an environment."""

    jobs: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once."""
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.jobs = workflow["jobs"]

    def test_release_job_uses_the_release_environment(self) -> None:
        """The write token reaches the release job only through the release environment."""
        self.assertEqual(self.jobs[RELEASE_JOB].get("environment"), RELEASE_ENVIRONMENT)

    def test_no_other_job_declares_an_environment(self) -> None:
        """Only the release job waits on an environment."""
        for name, job in self.jobs.items():
            if name == RELEASE_JOB:
                continue
            self.assertNotIn("environment", job, name)


if __name__ == "__main__":
    unittest.main()
