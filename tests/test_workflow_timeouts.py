"""Checks that every workflow job on a runner declares a timeout."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"
# The two review files pin the reusable review workflows. The repository owner
# approves each edit to them separately. scripts/check_compliance_tree.py accepts
# only an exact key set for the privileged pull_request_target job, so a timeout
# there fails that check. This check leaves all three files out.
PINNED_WORKFLOWS = frozenset(
    {"security-review-pr.yml", "quality-review-pr.yml", "immutable-conflict-check.yml"}
)
# GitHub stops a job without a timeout after six hours.
RUNNER_DEFAULT_TIMEOUT_MINUTES = 360
TIMEOUT_KEY = "timeout-minutes"


def runner_jobs() -> list[tuple[str, dict[str, Any]]]:
    """Return (workflow:job, job) for each job that runs steps on a runner.

    A job that calls a reusable workflow with uses: cannot set a timeout. The
    called workflow sets the timeout of its own jobs.
    """
    jobs: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name in PINNED_WORKFLOWS:
            continue
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in workflow["jobs"].items():
            if "uses" not in job:
                jobs.append((f"{path.name}:{name}", job))
    return jobs


class WorkflowTimeoutTest(unittest.TestCase):
    """A hung step ends at a stated limit instead of the six hour runner default."""

    def test_every_runner_job_declares_a_timeout(self) -> None:
        missing = [name for name, job in runner_jobs() if TIMEOUT_KEY not in job]
        self.assertEqual(missing, [])

    def test_each_timeout_is_a_whole_minute_count_below_the_default(self) -> None:
        for name, job in runner_jobs():
            with self.subTest(job=name):
                timeout = job.get(TIMEOUT_KEY)
                self.assertIsInstance(timeout, int)
                self.assertGreater(timeout, 0)
                self.assertLess(timeout, RUNNER_DEFAULT_TIMEOUT_MINUTES)


if __name__ == "__main__":
    unittest.main()
