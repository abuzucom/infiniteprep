"""Behavioral checks for the workflow wiring that skips heavy jobs on light changes."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"
CLASSIFY_COMMAND = 'python3 scripts/classify_changed_paths.py --base "$BASE_SHA" --head "$HEAD_SHA"'
BASE_SHA_SOURCE = "${{ github.event.pull_request.base.sha || github.event.before }}"
HEAD_SHA_SOURCE = "${{ github.event.pull_request.head.sha || github.sha }}"
LIGHT_OUTPUT = "${{ steps.classify.outputs.light }}"
NOT_CANCELLED = "!cancelled()"
NOT_LIGHT = "needs.changes.outputs.light != 'true'"
LINT_SUCCEEDED = "needs.lint-and-style.result == 'success'"
# Each workflow maps to the jobs that a light change skips.
SKIPPED_JOBS = {
    "ci.yml": ("test-matrix",),
    "security-devops.yml": ("msdo",),
    "dependency-review.yml": ("dependency-review",),
}
ALWAYS_RUNNING_CI_JOBS = ("changes", "checks", "lint-and-style", "agents-lint")


def load_jobs(workflow_name: str) -> dict[str, Any]:
    """Return the parsed jobs of one workflow."""
    text = (WORKFLOWS / workflow_name).read_text(encoding="utf-8")
    return dict(yaml.safe_load(text)["jobs"])


def list_needs(job: dict[str, Any]) -> list[str]:
    """Return the needs of a job as a list."""
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


class ChangesJobTest(unittest.TestCase):
    """Each workflow classifies the change with the repository script."""

    def test_changes_job_exports_the_light_flag(self) -> None:
        """The job output reads the light flag of the classify step."""
        for workflow_name in SKIPPED_JOBS:
            with self.subTest(workflow=workflow_name):
                job = load_jobs(workflow_name)["changes"]
                self.assertEqual(job["outputs"], {"light": LIGHT_OUTPUT})
                self.assertNotIn("if", job)

    def test_checkout_fetches_the_whole_history(self) -> None:
        """The merge base lookup needs the complete history and no credentials."""
        for workflow_name in SKIPPED_JOBS:
            with self.subTest(workflow=workflow_name):
                steps = load_jobs(workflow_name)["changes"]["steps"]
                checkout = steps[0]
                self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
                self.assertEqual(checkout["with"]["fetch-depth"], 0)
                self.assertIs(checkout["with"]["persist-credentials"], False)

    def test_classify_step_passes_revisions_through_the_environment(self) -> None:
        """Event data reaches the script as environment values, never as shell text."""
        for workflow_name in SKIPPED_JOBS:
            with self.subTest(workflow=workflow_name):
                steps = load_jobs(workflow_name)["changes"]["steps"]
                classify = next(step for step in steps if step.get("id") == "classify")
                self.assertEqual(classify["run"], CLASSIFY_COMMAND)
                self.assertEqual(
                    classify["env"],
                    {"BASE_SHA": BASE_SHA_SOURCE, "HEAD_SHA": HEAD_SHA_SOURCE},
                )


class SkippedJobTest(unittest.TestCase):
    """Heavy jobs skip on a light change and run in every other case."""

    def test_heavy_jobs_wait_for_the_class(self) -> None:
        """Each heavy job depends on the changes job."""
        for workflow_name, job_names in SKIPPED_JOBS.items():
            jobs = load_jobs(workflow_name)
            for job_name in job_names:
                with self.subTest(workflow=workflow_name, job=job_name):
                    self.assertIn("changes", list_needs(jobs[job_name]))

    def test_heavy_jobs_run_unless_the_class_is_light(self) -> None:
        """A failed or skipped changes job yields no flag, and the heavy job runs."""
        for workflow_name, job_names in SKIPPED_JOBS.items():
            jobs = load_jobs(workflow_name)
            for job_name in job_names:
                with self.subTest(workflow=workflow_name, job=job_name):
                    condition = jobs[job_name]["if"]
                    self.assertIn(NOT_CANCELLED, condition)
                    self.assertIn(NOT_LIGHT, condition)

    def test_test_matrix_still_requires_a_clean_lint(self) -> None:
        """The status function replaces the implicit success check, so lint is explicit."""
        job = load_jobs("ci.yml")["test-matrix"]
        self.assertIn("lint-and-style", list_needs(job))
        self.assertIn(LINT_SUCCEEDED, job["if"])

    def test_cheap_ci_jobs_always_run(self) -> None:
        """The classifier, the wiring tests, and both lint jobs carry no condition."""
        jobs = load_jobs("ci.yml")
        for job_name in ALWAYS_RUNNING_CI_JOBS:
            with self.subTest(job=job_name):
                self.assertNotIn("if", jobs[job_name])


if __name__ == "__main__":
    unittest.main()
