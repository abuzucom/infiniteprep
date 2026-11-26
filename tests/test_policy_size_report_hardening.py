#!/usr/bin/env python3
"""Exercise limit pinning and comment serialization for the size report."""
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import sync

from scripts import check_policy_size

WORKFLOW = ROOT / ".github" / "workflows" / "policy-size-report.yml"
AGENTS_LIMIT = 32 * 1024
SPOOFED_LIMIT = 10 * 1024 * 1024
SAMPLE_AGENTS_BYTES = 40000
SAMPLE_ASSEMBLED_BYTES = 70000


def _build_report(agents_limit: int, assembled_limit: int) -> dict:
    """Return an over-limit report carrying the given limits."""
    return {"schema": 1, "targets": [
        {"name": "AGENTS.md", "bytes": SAMPLE_AGENTS_BYTES,
         "limit": agents_limit},
        {"name": "assembled policy", "bytes": SAMPLE_ASSEMBLED_BYTES,
         "limit": assembled_limit},
    ]}


class LimitPinningTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.report_path = Path(self.temporary_directory.name) / "report.json"

    def _render(self, report: dict) -> tuple:
        """Write the report as JSON and return the renderer exit code and streams."""
        self.report_path.write_text(json.dumps(report), encoding="ascii")
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = check_policy_size.main(
                ["--render-markdown", str(self.report_path)])
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_canonical_limits_render_over_limit(self):
        exit_code, stdout, _ = self._render(
            _build_report(AGENTS_LIMIT, sync.MAX_POLICY_BYTES))
        self.assertEqual(exit_code, 0)
        self.assertIn("Over limit", stdout)

    def test_spoofed_limits_are_rejected(self):
        for report in (_build_report(SPOOFED_LIMIT, sync.MAX_POLICY_BYTES),
                       _build_report(AGENTS_LIMIT, SPOOFED_LIMIT)):
            with self.subTest(report=report):
                exit_code, stdout, stderr = self._render(report)
                self.assertEqual(exit_code, 1)
                self.assertEqual(stdout, "")
                self.assertIn("invalid size report", stderr)
                self.assertIn("limit", stderr)


class CommentSerializationTests(unittest.TestCase):
    def test_branch_runs_share_queue(self):
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        concurrency = workflow["concurrency"]
        group = concurrency["group"]
        self.assertIn("github.event.workflow_run.head_repository.full_name", group)
        self.assertIn("github.event.workflow_run.head_branch", group)
        self.assertNotIn("head_sha", group)
        self.assertIs(concurrency["cancel-in-progress"], False)


if __name__ == "__main__":
    unittest.main()
