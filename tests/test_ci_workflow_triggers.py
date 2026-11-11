"""Behavioral checks for the ci workflow triggers and run cancellation."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/ci.yml"
PRIMARY_BRANCH = "main"
PULL_REQUEST_ONLY = "${{ github.event_name == 'pull_request' }}"
PULL_REQUEST_NUMBER = "github.event.pull_request.number"
COMMIT_SHA = "github.sha"


class CiWorkflowTriggerTest(unittest.TestCase):
    """Each pull request commit starts one ci run, and a newer commit replaces it."""

    workflow: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once for every check."""
        cls.workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

    def test_push_runs_on_the_primary_branch_only(self) -> None:
        """A push to a pull request branch starts no second ci run."""
        self.assertEqual(self.workflow["on"]["push"]["branches"], [PRIMARY_BRANCH])

    def test_pull_request_trigger_stays_unfiltered(self) -> None:
        """The review callers need a pull_request ci run for every pull request."""
        triggers = self.workflow["on"]
        self.assertIn("pull_request", triggers)
        self.assertIsNone(triggers["pull_request"])

    def test_only_pull_request_runs_cancel(self) -> None:
        """A run on the primary branch always completes."""
        concurrency = self.workflow["concurrency"]
        self.assertEqual(concurrency["cancel-in-progress"], PULL_REQUEST_ONLY)

    def test_group_separates_pull_requests_and_commits(self) -> None:
        """One group per pull request, and one group per pushed commit."""
        group = self.workflow["concurrency"]["group"]
        self.assertIn(PULL_REQUEST_NUMBER, group)
        self.assertIn(COMMIT_SHA, group)
        self.assertLess(group.index(PULL_REQUEST_NUMBER), group.index(COMMIT_SHA))


if __name__ == "__main__":
    unittest.main()
