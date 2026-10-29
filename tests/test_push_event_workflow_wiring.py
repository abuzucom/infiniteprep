"""Cover the workflow wiring that keeps push events off pull request paths."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Final

import yaml

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
WORKFLOWS: Final[Path] = ROOT / ".github" / "workflows"
SYNC_WORKFLOW: Final[Path] = WORKFLOWS / "sync-check.yml"
SECURITY_CALLER: Final[Path] = WORKFLOWS / "security-review-pr.yml"
SUITE_COMMANDS: Final[tuple[str, ...]] = (
    "python scripts/run_tests.py",
    "python scripts/check_hook_coverage.py",
)
POSIX_UNSET_SHELL: Final[str] = "env -u GITHUB_EVENT_PATH bash -e {0}"
PWSH_REMOVE_COMMAND: Final[str] = "Remove-Item Env:GITHUB_EVENT_PATH"
PULL_REQUEST_GUARD: Final[str] = "github.event.workflow_run.event == 'pull_request'"


def load_workflow(path: Path) -> dict[str, Any]:
    """Return the parsed workflow, failing when the file is absent."""
    if not path.is_file():
        raise AssertionError(f"missing workflow: {path.relative_to(ROOT)}")
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def find_suite_steps(workflow: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return (job name, step) pairs whose run command executes the test suite."""
    found: list[tuple[str, dict[str, Any]]] = []
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if str(step.get("run", "")).strip() in SUITE_COMMANDS:
                found.append((job_name, step))
    return found


class SyncCheckEventPathTest(unittest.TestCase):
    """Every sync-check suite run drops the push event payload."""

    def setUp(self) -> None:
        self.workflow = load_workflow(SYNC_WORKFLOW)
        self.suite_steps = find_suite_steps(self.workflow)

    def test_each_suite_command_runs_in_a_step(self) -> None:
        commands = [str(step["run"]).strip() for _, step in self.suite_steps]
        self.assertEqual(commands.count(SUITE_COMMANDS[0]), 2)
        self.assertEqual(commands.count(SUITE_COMMANDS[1]), 1)

    def test_windows_suite_step_removes_the_event_path_in_pwsh(self) -> None:
        windows_steps = [step for job, step in self.suite_steps if job == "tests-windows"]
        self.assertEqual(len(windows_steps), 1)
        shell = str(windows_steps[0].get("shell", ""))
        self.assertTrue(shell.startswith("pwsh -command "), shell)
        self.assertIn(PWSH_REMOVE_COMMAND, shell)
        self.assertIn(". '{0}'", shell)
        self.assertIn("exit $LASTEXITCODE", shell)

    def test_posix_suite_steps_unset_the_event_path(self) -> None:
        posix_steps = [step for job, step in self.suite_steps if job != "tests-windows"]
        self.assertEqual(len(posix_steps), 2)
        for step in posix_steps:
            self.assertEqual(step.get("shell"), POSIX_UNSET_SHELL, step["run"])


class SecurityReviewPushGuardTest(unittest.TestCase):
    """The security review resolves a pull request only for pull request runs."""

    def test_resolve_job_skips_push_triggered_ci_runs(self) -> None:
        workflow = load_workflow(SECURITY_CALLER)
        self.assertEqual(workflow["jobs"]["resolve-pr"].get("if"), PULL_REQUEST_GUARD)

    def test_review_jobs_still_require_a_resolved_pull_request(self) -> None:
        jobs = load_workflow(SECURITY_CALLER)["jobs"]
        for job_name in ("review", "fork-review", "fork-review-skipped"):
            self.assertIn("needs.resolve-pr.result == 'success'", jobs[job_name]["if"])


if __name__ == "__main__":
    unittest.main()
