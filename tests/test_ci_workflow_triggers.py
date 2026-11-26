"""Behavioral checks for the ci workflow triggers and run cancellation."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/ci.yml"
# The pull_request trigger is the only trigger, so every run carries a pull request number.
CONCURRENCY_GROUP = "ci-${{ github.event.pull_request.number }}"


class CiWorkflowTriggerTest(unittest.TestCase):
    """Each pull request commit starts one ci run, and a newer commit replaces it."""

    workflow: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once for every check."""
        cls.workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

    def test_push_starts_no_run(self) -> None:
        """A push to any branch, main included, starts no ci run."""
        self.assertNotIn("push", self.workflow["on"])

    def test_pull_request_trigger_stays_unfiltered(self) -> None:
        """The review callers need a pull_request ci run for every pull request."""
        triggers = self.workflow["on"]
        self.assertIn("pull_request", triggers)
        self.assertIsNone(triggers["pull_request"])

    def test_newer_commit_cancels_older_run(self) -> None:
        """Every run belongs to a pull request, so a newer commit always replaces it."""
        self.assertIs(self.workflow["concurrency"]["cancel-in-progress"], True)

    def test_group_keys_on_pull_request(self) -> None:
        """One group per pull request."""
        self.assertEqual(self.workflow["concurrency"]["group"], CONCURRENCY_GROUP)


if __name__ == "__main__":
    unittest.main()
