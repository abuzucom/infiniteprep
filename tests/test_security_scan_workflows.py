"""Cover the pins, permissions, and checkout credentials of the security scan workflows."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any, Final

import yaml

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
WORKFLOWS: Final[Path] = ROOT / ".github" / "workflows"
DEPENDENCY_REVIEW: Final[Path] = WORKFLOWS / "dependency-review.yml"
SECURITY_DEVOPS: Final[Path] = WORKFLOWS / "security-devops.yml"
ADVANCED_CODEQL: Final[Path] = WORKFLOWS / "codeql.yml"
FULL_SHA_PIN: Final[re.Pattern[str]] = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
CHECKOUT_ACTION: Final[str] = "actions/checkout@"
# Each pin names the commit of the release tag the maintainer approved under Rule 9.
APPROVED_PINS: Final[dict[str, str]] = {
    "actions/dependency-review-action": "3b139cfc5fae8b618d3eae3675e383bb1769c019",
    "github/codeql-action/upload-sarif": "b56ba49b26e50535fa1e7f7db0f4f7b4bf65d80d",
    "microsoft/security-devops-action": "cf28f87c67770dbaa208cce3665fafc849f77507",
}
# PyYAML reads the bare `on` key as the boolean True.
TRIGGER_KEY: Final[bool] = True


def load_workflow(path: Path) -> dict[Any, Any]:
    """Return the parsed workflow, failing when the file is absent."""
    if not path.is_file():
        raise AssertionError(f"missing workflow: {path.relative_to(ROOT)}")
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def collect_steps(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    """Return every step of every job in the workflow."""
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


class SecurityScanWorkflowTest(unittest.TestCase):
    """Both scan workflows pin actions, scope tokens, and drop checkout credentials."""

    def test_every_action_is_pinned_to_a_full_commit_sha(self) -> None:
        for path in (DEPENDENCY_REVIEW, SECURITY_DEVOPS):
            for step in collect_steps(load_workflow(path)):
                if "uses" in step:
                    self.assertRegex(str(step["uses"]), FULL_SHA_PIN, path.name)

    def test_each_scan_action_uses_its_approved_commit(self) -> None:
        pinned: dict[str, str] = {}
        for path in (DEPENDENCY_REVIEW, SECURITY_DEVOPS):
            for step in collect_steps(load_workflow(path)):
                action, _, commit = str(step.get("uses", "")).partition("@")
                if action in APPROVED_PINS:
                    pinned[action] = commit
        self.assertEqual(pinned, APPROVED_PINS)

    def test_checkout_steps_do_not_persist_credentials(self) -> None:
        for path in (DEPENDENCY_REVIEW, SECURITY_DEVOPS):
            checkouts = [
                step
                for step in collect_steps(load_workflow(path))
                if str(step.get("uses", "")).startswith(CHECKOUT_ACTION)
            ]
            self.assertEqual(len(checkouts), 1, path.name)
            self.assertIs(checkouts[0]["with"]["persist-credentials"], False, path.name)

    def test_top_level_token_is_read_only(self) -> None:
        for path in (DEPENDENCY_REVIEW, SECURITY_DEVOPS):
            self.assertEqual(load_workflow(path)["permissions"], {"contents": "read"}, path.name)

    def test_dependency_review_runs_only_on_pull_requests_with_read_access(self) -> None:
        workflow = load_workflow(DEPENDENCY_REVIEW)
        self.assertEqual(list(workflow[TRIGGER_KEY]), ["pull_request"])
        job = workflow["jobs"]["dependency-review"]
        self.assertEqual(job["permissions"], {"contents": "read", "pull-requests": "read"})

    def test_security_devops_uploads_its_own_sarif_output(self) -> None:
        workflow = load_workflow(SECURITY_DEVOPS)
        job = workflow["jobs"]["msdo"]
        self.assertEqual(job["permissions"]["security-events"], "write")
        upload = collect_steps(workflow)[-1]
        self.assertEqual(upload["with"]["sarif_file"], "${{ steps.msdo.outputs.sarifFile }}")

    def test_no_advanced_codeql_workflow_competes_with_default_setup(self) -> None:
        self.assertFalse(ADVANCED_CODEQL.exists())


if __name__ == "__main__":
    unittest.main()
