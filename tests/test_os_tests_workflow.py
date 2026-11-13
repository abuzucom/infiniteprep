"""Behavioral checks for the cross-platform gate test workflow and its split from sync-check."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"
OS_TESTS_WORKFLOW = WORKFLOWS / "os-tests.yml"
SYNC_WORKFLOW = WORKFLOWS / "sync-check.yml"
OS_TEST_JOBS = {"tests-windows": "windows-latest", "tests-macos": "macos-latest"}
PRIMARY_BRANCH = "main"
# A title or description edit changes no file, so it starts no cross-platform run.
CODE_EVENT_TYPES = ["opened", "synchronize", "reopened", "ready_for_review"]
EDITED_EVENT_TYPE = "edited"
CLASSIFY_COMMAND = 'python3 scripts/classify_changed_paths.py --base "$BASE_SHA" --head "$HEAD_SHA"'
REVISION_SOURCES = {
    "BASE_SHA": "${{ github.event.pull_request.base.sha || github.event.before }}",
    "HEAD_SHA": "${{ github.event.pull_request.head.sha || github.sha }}",
}
LIGHT_OUTPUT = "${{ steps.classify.outputs.light }}"
NOT_CANCELLED = "!cancelled()"
JOB_NOT_LIGHT = "needs.changes.outputs.light != 'true'"
STEP_NOT_LIGHT = "steps.classify.outputs.light != 'true'"
AGENT_LINT_ACTION = "0xmariowu/AgentLint@"
PULL_REQUEST_ONLY = "${{ github.event_name == 'pull_request' }}"
PULL_REQUEST_NUMBER = "github.event.pull_request.number"
COMMIT_SHA = "github.sha"
# os-tests.yml quotes its `on` key. sync-check.yml leaves it bare, and PyYAML
# reads the bare key as the boolean True.
OS_TESTS_TRIGGER_KEY = "on"
SYNC_TRIGGER_KEY = True


def load_workflow(path: Path) -> dict[Any, Any]:
    """Return the parsed workflow, failing when the file is absent."""
    if not path.is_file():
        raise AssertionError(f"missing workflow: {path.relative_to(REPOSITORY_ROOT)}")
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def find_classify_step(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the classify step of a job."""
    return next(step for step in steps if step.get("id") == "classify")


class OsTestsTriggerTest(unittest.TestCase):
    """The cross-platform tests run for code events only."""

    def setUp(self) -> None:
        """Parse the workflow."""
        self.workflow = load_workflow(OS_TESTS_WORKFLOW)
        self.triggers = self.workflow[OS_TESTS_TRIGGER_KEY]

    def test_runs_on_pushes_to_the_primary_branch(self) -> None:
        """A merged change still gets its cross-platform run."""
        self.assertEqual(self.triggers["push"]["branches"], [PRIMARY_BRANCH])

    def test_pull_request_edits_start_no_run(self) -> None:
        """The pull request trigger lists the code events and omits edited."""
        pull_request = self.triggers["pull_request"]
        self.assertEqual(pull_request["branches"], [PRIMARY_BRANCH])
        self.assertEqual(pull_request["types"], CODE_EVENT_TYPES)

    def test_newer_run_replaces_the_older_run(self) -> None:
        """A newer pull request commit replaces the older run, and a main run completes."""
        concurrency = self.workflow["concurrency"]
        self.assertEqual(concurrency["cancel-in-progress"], PULL_REQUEST_ONLY)
        group = concurrency["group"]
        self.assertIn(PULL_REQUEST_NUMBER, group)
        self.assertIn(COMMIT_SHA, group)
        self.assertLess(group.index(PULL_REQUEST_NUMBER), group.index(COMMIT_SHA))

    def test_token_is_read_only(self) -> None:
        """The workflow token reads repository contents and nothing else."""
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})


class OsTestsJobTest(unittest.TestCase):
    """Both platform jobs skip on a light change and run in every other case."""

    def setUp(self) -> None:
        """Parse the jobs."""
        self.jobs = load_workflow(OS_TESTS_WORKFLOW)["jobs"]

    def test_changes_job_classifies_the_change(self) -> None:
        """The changes job runs the classifier and exports the light flag."""
        job = self.jobs["changes"]
        self.assertNotIn("if", job)
        self.assertEqual(job["outputs"], {"light": LIGHT_OUTPUT})
        self.assertEqual(job["steps"][0]["with"]["fetch-depth"], 0)
        classify = find_classify_step(job["steps"])
        self.assertEqual(classify["run"], CLASSIFY_COMMAND)
        self.assertEqual(classify["env"], REVISION_SOURCES)

    def test_each_platform_job_runs_on_its_platform(self) -> None:
        """The Windows and macOS jobs keep their runner images."""
        for job_name, runner in OS_TEST_JOBS.items():
            with self.subTest(job=job_name):
                self.assertEqual(self.jobs[job_name]["runs-on"], runner)

    def test_platform_jobs_run_unless_the_class_is_light(self) -> None:
        """A failed changes job yields no flag, and the platform jobs run."""
        for job_name in OS_TEST_JOBS:
            with self.subTest(job=job_name):
                job = self.jobs[job_name]
                self.assertEqual(job["needs"], "changes")
                self.assertIn(NOT_CANCELLED, job["if"])
                self.assertIn(JOB_NOT_LIGHT, job["if"])

    def test_every_checkout_drops_credentials(self) -> None:
        """Each checkout sets persist-credentials to false (Rule 11)."""
        steps = [step for job in self.jobs.values() for step in job["steps"]]
        checkouts = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), len(self.jobs))
        for checkout in checkouts:
            self.assertIs(checkout["with"]["persist-credentials"], False)


class SyncCheckSplitTest(unittest.TestCase):
    """sync-check keeps the pull request text checks and loses the platform jobs."""

    def setUp(self) -> None:
        """Parse the workflow."""
        self.workflow = load_workflow(SYNC_WORKFLOW)
        self.jobs = self.workflow["jobs"]

    def test_platform_jobs_left_sync_check(self) -> None:
        """An edited event in sync-check can no longer cancel or mask a platform run."""
        for job_name in OS_TEST_JOBS:
            with self.subTest(job=job_name):
                self.assertNotIn(job_name, self.jobs)

    def test_sync_check_still_runs_on_edits(self) -> None:
        """The pull request title and description checks rerun after an edit."""
        types = self.workflow[SYNC_TRIGGER_KEY]["pull_request"]["types"]
        self.assertIn(EDITED_EVENT_TYPE, types)

    def test_agent_lint_skips_on_a_light_change(self) -> None:
        """The advisory lint reads the light flag of the classify step in its own job."""
        steps = self.jobs["check-sync"]["steps"]
        classify = find_classify_step(steps)
        self.assertEqual(classify["run"], CLASSIFY_COMMAND)
        self.assertEqual(classify["env"], REVISION_SOURCES)
        self.assertNotIn("continue-on-error", classify)
        agent_lint = next(
            step for step in steps if step.get("uses", "").startswith(AGENT_LINT_ACTION)
        )
        self.assertEqual(agent_lint["if"], STEP_NOT_LIGHT)
        self.assertLess(steps.index(classify), steps.index(agent_lint))

    def test_no_other_sync_check_step_reads_the_light_flag(self) -> None:
        """Every blocking check in check-sync runs on a light change."""
        steps = self.jobs["check-sync"]["steps"]
        gated = [step["name"] for step in steps if STEP_NOT_LIGHT in str(step.get("if", ""))]
        self.assertEqual(gated, ["AgentLint (advisory)"])


if __name__ == "__main__":
    unittest.main()
