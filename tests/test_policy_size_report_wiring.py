#!/usr/bin/env python3
"""Check the pull request policy size report workflow wiring."""
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import sync

WORKFLOW = ROOT / ".github" / "workflows" / "policy-size-report.yml"
UPSTREAM_WORKFLOW = "Convention Sync Check"


def _load_workflow() -> dict:
    """Return the parsed report workflow."""
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _read_triggers(workflow: dict) -> dict:
    """Return the trigger map. PyYAML reads the bare `on` key as True."""
    return workflow.get("on", workflow.get(True))


class PolicySizeReportWiringTests(unittest.TestCase):
    def test_workflow_runs_after_sync(self):
        workflow = _load_workflow()
        triggers = _read_triggers(workflow)
        self.assertEqual(set(triggers), {"workflow_run"})
        self.assertEqual(triggers["workflow_run"]["workflows"], [UPSTREAM_WORKFLOW])
        self.assertEqual(triggers["workflow_run"]["types"], ["completed"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})

    def test_upstream_workflow_name_matches(self):
        text = (ROOT / ".github" / "workflows" / "sync-check.yml").read_text(
            encoding="utf-8")
        self.assertIn(f"name: {UPSTREAM_WORKFLOW}\n", text)

    def test_measure_job_read_only(self):
        job = _load_workflow()["jobs"]["measure"]
        self.assertEqual(
            job["permissions"], {"contents": "read", "pull-requests": "read"})
        checkout = [step for step in job["steps"]
                    if str(step.get("uses", "")).startswith("actions/checkout@")]
        self.assertEqual(len(checkout), 1)
        self.assertIs(checkout[0]["with"]["persist-credentials"], False)
        self.assertNotIn("ref", checkout[0]["with"])
        self.assertIn("comment", job["outputs"])

    def test_comment_job_runs_no_code(self):
        job = _load_workflow()["jobs"]["comment"]
        self.assertEqual(job["needs"], "measure")
        self.assertEqual(job["permissions"], {"pull-requests": "write"})
        for step in job["steps"]:
            with self.subTest(step=step.get("name")):
                self.assertNotIn("run", step)
                uses = str(step.get("uses", ""))
                self.assertFalse(uses.startswith("actions/checkout@"))
                self.assertFalse(uses.startswith("actions/setup-python@"))
        environments = [step.get("env", {}) for step in job["steps"]]
        self.assertIn("${{ needs.measure.outputs.comment }}",
                      [value for env in environments for value in env.values()])

    def test_comment_job_sole_writer(self):
        for name, job in _load_workflow()["jobs"].items():
            with self.subTest(job=name):
                writes = [scope for scope, level in job.get("permissions", {}).items()
                          if level == "write"]
                expected = ["pull-requests"] if name == "comment" else []
                self.assertEqual(writes, expected)

    def test_report_job_sticky_comment(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("python scripts/check_policy_size.py", text)
        self.assertIn("--json", text)
        self.assertIn("--render-markdown", text)
        self.assertIn("<!-- policy-size-report -->", text)
        self.assertNotIn("labels", text)

    def test_workflow_lists_every_policy_file(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        names = ((sync.SOURCE,) + sync.ADOPTER_POLICY_FILES
                 + sync.SOURCE_ONLY_POLICY_FILES + tuple(sync.COPIES))
        for name in names:
            with self.subTest(name=name):
                self.assertIn(f"'{name}'", text)

    def test_size_checker_gates_prs(self):
        text = (ROOT / ".github" / "workflows" / "sync-check.yml").read_text(
            encoding="utf-8")
        self.assertIn("run: python scripts/check_policy_size.py\n", text)


if __name__ == "__main__":
    unittest.main()
